"""Verify the published evidence without model access or third-party packages."""
from pathlib import Path
from collections import Counter
import hashlib
import itertools
import json
import math
import statistics

ROOT = Path(__file__).resolve().parents[1]


def read(relative):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def close(actual, expected):
    if actual is None or expected is None:
        assert actual is expected, (actual, expected)
    else:
        assert math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-10), (actual, expected)


def file_in_repo(relative):
    path = (ROOT / relative).resolve()
    assert path.is_relative_to(ROOT), relative
    assert path.is_file() and not path.is_symlink(), relative
    return path


def verify_hash(relative, expected):
    actual = hashlib.sha256(file_in_repo(relative).read_bytes()).hexdigest()
    assert actual == expected, f"Changed archival file: {relative}"


def estimate_cost(usage, rate, threshold):
    total = usage["totals"]
    request_known = usage.get("request_breakdowns_complete", False)
    parts = usage["requests"] if request_known else [total]
    if request_known:
        for key in ["inputTokens", "cachedInputTokens", "outputTokens", "reasoningOutputTokens", "totalTokens"]:
            assert sum(part[key] for part in parts) == total[key], key
    low = high = 0.0
    for part in parts:
        for key in ["inputTokens", "cachedInputTokens", "outputTokens", "reasoningOutputTokens", "totalTokens"]:
            assert type(part[key]) is int and part[key] >= 0
        assert part["totalTokens"] == part["inputTokens"] + part["outputTokens"]
        assert part["cachedInputTokens"] <= part["inputTokens"]
        assert part["reasoningOutputTokens"] <= part["outputTokens"]
        write_known = type(part.get("cacheWriteInputTokens")) is int
        for upper in [False, True]:
            writes = part["cacheWriteInputTokens"] if write_known else part["inputTokens"] - part["cachedInputTokens"] if upper else 0
            assert 0 <= writes <= part["inputTokens"] - part["cachedInputTokens"]
            long = request_known and part["inputTokens"] > threshold
            long = long or (upper and not request_known and total["inputTokens"] > threshold)
            input_multiplier, output_multiplier = (2, 1.5) if long else (1, 1)
            cost = ((part["inputTokens"] - part["cachedInputTokens"] - writes) * rate["input"] * input_multiplier
                    + part["cachedInputTokens"] * rate["cached_input"] * input_multiplier
                    + writes * rate["cache_write"] * input_multiplier
                    + part["outputTokens"] * rate["output"] * output_multiplier) / 1_000_000
            if upper:
                high += cost
            else:
                low += cost
    exact = bool(usage["complete"]) and abs(high - low) < 1e-12
    return low, high, low if exact else None


def main():
    archive = read("results/archive-manifest.json")
    for relative, expected in archive.items():
        verify_hash(relative, expected)
    data = read("results/results.json")
    rows = data["runs"]
    assert len(rows) == 216 == len({r["run_id"] for r in rows})
    expected_cells = set(itertools.product(data["models"], data["reasoning_efforts"], [t["id"] for t in data["tasks"]], [1, 2]))
    actual_cells = {(r["model"], r["reasoning_effort"], r["task"], r["repetition"]) for r in rows}
    assert expected_cells == actual_cells and len(actual_cells) == 216
    assert Counter(r["status"] for r in rows) == {"completed": 214, "model_failed": 1, "timeout": 1}
    accepted = sum(r["status"] == "completed" and bool(r["grading"]["accepted"]) for r in rows)
    assert accepted == 213 == data["metadata"]["main_accepted_and_completed"]
    manifests = read("results/candidate-manifests.json")
    assert set(manifests) == {r["run_id"] for r in rows}
    files = 0
    for row in rows:
        record = manifests[row["run_id"]]
        assert row["snapshot"] == record["directory"]
        assert record["original_manifest_sha256"] == row["candidate_manifest_sha256"]
        encoded_manifest = json.dumps(record["files"], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        assert hashlib.sha256(encoded_manifest).hexdigest() == record["original_manifest_sha256"], row["run_id"]
        candidate = ROOT / record["directory"]
        actual_files = {p.relative_to(candidate).as_posix() for p in candidate.rglob("*")
                        if p.is_file() and "__pycache__" not in p.parts}
        assert actual_files == set(record["files"]), row["run_id"]
        for relative, expected in record["files"].items():
            verify_hash(f"{record['directory']}/{relative}", expected)
            files += 1
        assert row["inference_turns_submitted"] == 1
        assert row["measured_model"] == row["model"]
        assert row["requested_effort"] == row["reasoning_effort"]
        assert row["effective"]["effort"] == row["reasoning_effort"]
        assert row["requested_speed"] == "standard"
        assert not row["invalid_reasons"]
        bounds = estimate_cost(row["usage"], data["pricing"][row["model"]]["api_usd_per_million"], data["api_estimate"]["long_context_threshold_input_tokens_per_request"])
        for field, expected in zip(["api_usd_low", "api_usd_high", "api_usd_estimated"], bounds):
            close(row["costs"][field], expected)
        assert row["main_accepted_and_completed"] == (row["status"] == "completed" and row["grading"]["accepted"])
    close(data["metadata"]["known_observed_api_usd_subtotal"], sum(r["costs"]["api_usd_low"] for r in rows))
    assert sum(r["costs"]["api_estimate_complete"] for r in rows) == 214
    assert len(data["summary"]) == 18
    for summary in data["summary"]:
        group = [r for r in rows if r["model"] == summary["model"] and r["reasoning_effort"] == summary["reasoning_effort"]]
        assert len(group) == 12 == summary["expected"] == summary["attempted"]
        assert sum(r["main_accepted_and_completed"] for r in group) == summary["accepted"]
        close(summary["model_seconds_mean"], statistics.mean(r["model_elapsed_seconds"] for r in group))
        close(summary["api_usd_low_observed"], sum(r["costs"]["api_usd_low"] for r in group))
        close(summary["api_usd_total"], sum(r["costs"]["api_usd_estimated"] for r in group) if all(r["costs"]["api_usd_estimated"] is not None for r in group) else None)
        task_means = [statistics.mean(r["grading"]["score"] for r in group if r["task"] == task["id"]) for task in data["tasks"]]
        close(summary["quality_macro_mean"], statistics.mean(task_means))
    quality = read("results/quality-v2.1.json")
    strength = read("results/complex-test-strength.json")
    assert len(quality["runs"]) == 144 and len(strength["evaluations"]) == 72
    assert {r["run_id"] for r in quality["runs"]}.isdisjoint({r["run_id"] for r in strength["evaluations"]})
    by_id = {r["run_id"]: r for r in rows}
    for evaluation in quality["runs"]:
        assert evaluation["snapshot_manifest_sha256"] == by_id[evaluation["run_id"]]["candidate_manifest_sha256"]
        assert evaluation["overall_quality_score"] is None
    for evaluation in strength["evaluations"]:
        assert evaluation["candidate_manifest_sha256"] == by_id[evaluation["run_id"]]["candidate_manifest_sha256"]
    provenance = read("results/provenance.json")
    assert files == provenance["candidate_file_count"] == 1202
    verify_hash(provenance["pdf_path"], provenance["pdf_sha256"])
    verify_hash(provenance["original_report_path"], provenance["original_report_sha256"])
    presentation = read("results/report-presentation.json")
    assert presentation["pdf"] == provenance["pdf_path"]
    assert presentation["pdf_sha256"] == provenance["pdf_sha256"]
    assert presentation["primary_axes"] == {
        "x": "API-equivalent USD per attempt", "y": "Main functional macro score / 100"}
    assert presentation["quality_scale"] == [0, 100]
    assert presentation["partial_cost_arrow_direction"] == "right"
    assert presentation["model_inference_calls"] == 0 and not presentation["measured_data_changed"]
    assert presentation["visual_qa_verified"] and presentation["text_geometry_violations"] == []
    assert presentation["visually_inspected_pages"] == list(range(1, presentation["pages"] + 1))
    for relative, expected in presentation["input_sha256"].items():
        verify_hash(relative, expected)
    print(json.dumps({"verified": True, "attempts":216, "accepted":accepted, "candidate_files":files,
                      "archive_files":len(archive), "exact_cost_attempts":214,
                      "presentation_pages":presentation["pages"], "original_report_preserved":True}, indent=2))


if __name__ == "__main__":
    main()
