"""Derive presentation facts from the frozen public evidence, without model calls.

The generated JSON is a view of existing measurements. It adds no code-quality
composite, rerun, rescore, confidence interval, or causal price attribution.
"""

from collections import Counter
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import statistics

from verify_archive import estimate_cost, verify_hash


ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    "results/results.json",
    "results/quality-v2.1.json",
    "results/complex-test-strength.json",
)
DEFECTS = (
    ("read_current_state", "Snapshot reads"),
    ("write_skew", "Write skew"),
    ("no_phantom_check", "Phantoms"),
    ("forget_reads_on_rollback", "Rollback reads"),
    ("recovery_accepts_gaps", "Recovery"),
    ("checkpoint_alias", "Checkpoint aliasing"),
)


def read(relative):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def exact_mean(rows):
    values = [row["costs"]["api_usd_estimated"] for row in rows]
    return statistics.mean(values) if all(v is not None for v in values) else None


def derive():
    """Return the same facts for the same source bytes in a stable order."""
    archive = read("results/archive-manifest.json")
    for relative in SOURCES:
        verify_hash(relative, archive[relative])
    data, quality, strength = map(read, SOURCES)
    rows = data["runs"]
    by_id = {r["run_id"]: r for r in rows}
    strength_by_id = {r["run_id"]: r for r in strength["evaluations"]}
    quality_by_id = {r["run_id"]: r for r in quality["runs"]}
    models, efforts = data["models"], data["reasoning_efforts"]
    tasks = [t["id"] for t in data["tasks"]]
    cells = list(itertools.product(models, efforts))

    def group(model, effort, task=None):
        return sorted(
            [r for r in rows if r["model"] == model
             and r["reasoning_effort"] == effort
             and (task is None or r["task"] == task)],
            key=lambda r: (tasks.index(r["task"]), r["repetition"]),
        )

    mvcc = []
    dag = []
    effort_settings = []
    low_groups = {model: group(model, "low") for model in models}
    for model, effort in cells:
        candidates = group(model, effort, "06-transactions")
        assert len(candidates) == 2
        repetitions = []
        for row in candidates:
            evaluation = strength_by_id[row["run_id"]]
            status = evaluation["status"]
            score = evaluation["score"]
            states = {}
            if status == "usable":
                assert set(evaluation["controls"]) == {d[0] for d in DEFECTS}
                killed = set(evaluation["killed"])
                assert evaluation["total_mutants"] == len(DEFECTS)
                for defect, _ in DEFECTS:
                    control = evaluation["controls"][defect]
                    assert control["usable"] and control["tests_run"] > 0
                    assert control["errors"] == 0 and control["skipped"] == 0
                    assert (control["failures"] > 0) == (defect in killed)
                    states[defect] = "detected" if defect in killed else "missed"
                killed_count = len(killed)
                assert abs(score - killed_count / len(DEFECTS) * 100) < 1e-10
            elif status == "no_added_tests":
                assert score == 0 and not evaluation["test_files"]
                killed_count = 0
                states = {defect: "no_added_tests" for defect, _ in DEFECTS}
            else:
                assert status == "unavailable" and score is None
                killed_count = None
                states = {defect: "unavailable" for defect, _ in DEFECTS}
            repetitions.append({
                "repetition": row["repetition"], "run_id": row["run_id"],
                "usd": row["costs"]["api_usd_estimated"], "score": score,
                "status": status, "killed_count": killed_count,
                "defect_states": states,
            })
        scores = [r["score"] for r in repetitions]
        available = sum(score is not None for score in scores)
        mvcc.append({
            "model": model, "effort": effort,
            "mean_usd": exact_mean(candidates),
            "main_scores": [r["grading"]["score"] for r in candidates],
            "accepted": sum(r["main_accepted_and_completed"] for r in candidates),
            "usable": available,
            "pair_test_score": statistics.mean(scores) if available == 2 else None,
            "repetitions": repetitions,
        })

        candidates = group(model, effort, "03-hard")
        perf_repetitions = []
        for row in candidates:
            perf = quality_by_id[row["run_id"]]["performance"]
            assert len(perf["samples_seconds"]) == 3
            assert perf["median_seconds"] == statistics.median(perf["samples_seconds"])
            perf_repetitions.append({
                "repetition": row["repetition"], "run_id": row["run_id"],
                "usd": row["costs"]["api_usd_estimated"],
                "runtime_ms": perf["median_seconds"] * 1000,
                "peak_kib": perf["peak_python_bytes"] / 1024,
            })
        dag.append({
            "model": model, "effort": effort, "mean_usd": exact_mean(candidates),
            "mean_runtime_ms": statistics.mean(r["runtime_ms"] for r in perf_repetitions),
            "mean_peak_kib": statistics.mean(r["peak_kib"] for r in perf_repetitions),
            "main_scores": [r["grading"]["score"] for r in candidates],
            "accepted": sum(r["main_accepted_and_completed"] for r in candidates),
            "repetitions": perf_repetitions,
        })

        candidates = group(model, effort)
        low = low_groups[model]
        mean_usd = exact_mean(candidates)
        mean_low_usd = statistics.mean(r["costs"]["api_usd_low"] for r in candidates)
        mean_seconds = statistics.mean(r["model_elapsed_seconds"] for r in candidates)
        low_usd = exact_mean(low)
        low_seconds = statistics.mean(r["model_elapsed_seconds"] for r in low)
        main_macro = statistics.mean(
            statistics.mean(r["grading"]["score"] for r in candidates if r["task"] == task)
            for task in tasks
        )
        effort_settings.append({
            "model": model, "effort": effort, "mean_usd": mean_usd,
            "mean_usd_low": mean_low_usd, "main_macro": main_macro,
            "accepted": sum(r["main_accepted_and_completed"] for r in candidates),
            "expected": len(candidates), "mean_agent_seconds": mean_seconds,
            "cost_multiple": mean_usd / low_usd if mean_usd is not None else None,
            "cost_multiple_low": mean_low_usd / low_usd,
            "time_multiple": mean_seconds / low_seconds,
        })

    pairs = []
    excluded_pairs = []
    for model, effort, task in itertools.product(models, efforts, tasks):
        candidates = group(model, effort, task)
        assert len(candidates) == 2
        if any(r["costs"]["api_usd_estimated"] is None for r in candidates):
            excluded_pairs.append({"model": model, "effort": effort, "task": task})
            continue
        values = [r["costs"]["api_usd_estimated"] for r in candidates]
        assert min(values) > 0
        pairs.append({
            "model": model, "effort": effort, "task": task,
            "runs": [{"repetition": r["repetition"], "run_id": r["run_id"],
                      "usd": r["costs"]["api_usd_estimated"],
                      "main_score": r["grading"]["score"],
                      "accepted": r["main_accepted_and_completed"]} for r in candidates],
            "ratio": max(values) / min(values),
        })
    repeat_cost = {
        "pairs": pairs, "excluded_pairs": excluded_pairs,
        "exact_pairs": len(pairs),
        "identical_main_pairs": sum(p["runs"][0]["main_score"] == p["runs"][1]["main_score"] for p in pairs),
        "over_1_5_pairs": sum(p["ratio"] > 1.5 for p in pairs),
        "ratio_definition": "More expensive repeat divided by cheaper repeat; two observations, not a confidence interval.",
    }
    assert (repeat_cost["exact_pairs"], repeat_cost["identical_main_pairs"], repeat_cost["over_1_5_pairs"]) == (106, 105, 20)

    old_model, new_model = "gpt-6-sol", "gpt-6.1-sol"
    keyed = {(r["model"], r["reasoning_effort"], r["task"], r["repetition"]): r for r in rows}
    repriced_rate = data["pricing"][new_model]["api_usd_per_million"]
    threshold = data["api_estimate"]["long_context_threshold_input_tokens_per_request"]
    matched, excluded_keys = [], []
    for effort, task, repetition in itertools.product(efforts, tasks, (1, 2)):
        old = keyed[old_model, effort, task, repetition]
        new = keyed[new_model, effort, task, repetition]
        if old["costs"]["api_usd_estimated"] is None or new["costs"]["api_usd_estimated"] is None:
            excluded_keys.append({"effort": effort, "task": task, "repetition": repetition,
                                  "old_run_id": old["run_id"], "new_run_id": new["run_id"],
                                  "reason": "At least one archived API estimate is partial."})
            continue
        _, _, repriced = estimate_cost(old["usage"], repriced_rate, threshold)
        assert repriced is not None
        matched.append({
            "effort": effort, "task": task, "repetition": repetition,
            "old_run_id": old["run_id"], "new_run_id": new["run_id"],
            "old_usd": old["costs"]["api_usd_estimated"],
            "repriced_usd": repriced, "new_usd": new["costs"]["api_usd_estimated"],
        })
    assert len(matched) == 70 and len(excluded_keys) == 2
    old_usd = sum(r["old_usd"] for r in matched)
    repriced_usd = sum(r["repriced_usd"] for r in matched)
    new_usd = sum(r["new_usd"] for r in matched)
    pricing_bridge = {
        "old_model": old_model, "new_model": new_model,
        "common_rate_model": new_model, "common_rates_usd_per_million": repriced_rate,
        "matched_n": len(matched), "excluded_keys": excluded_keys,
        "old_usd": old_usd, "repriced_usd": repriced_usd, "new_usd": new_usd,
        "tariff_reduction_usd": old_usd - repriced_usd,
        "usage_reduction_usd": repriced_usd - new_usd,
        "reduction_pct": (1 - new_usd / old_usd) * 100,
        "common_rate_reduction_pct": (1 - new_usd / repriced_usd) * 100,
        "matched_rows": matched,
        "interpretation": "Accounting counterfactual at frozen Standard API rates, not a causal experiment. Usage includes input, cache, output, and request-level long-context treatment.",
    }

    def setting(values, model, effort):
        return next(r for r in values if r["model"] == model and r["effort"] == effort)

    low_mvcc = setting(mvcc, new_model, "low")
    medium_mvcc = setting(mvcc, new_model, "medium")
    low_dag = setting(dag, new_model, "low")
    ultra_dag = setting(dag, new_model, "ultra")
    low_all = setting(effort_settings, new_model, "low")
    max_all = setting(effort_settings, new_model, "max")
    assert [r["killed_count"] for r in low_mvcc["repetitions"]] == [4, 5]
    assert [r["killed_count"] for r in medium_mvcc["repetitions"]] == [5, 6]
    assert all(setting(mvcc, new_model, effort)["pair_test_score"] == 100
               for effort in ("high", "xhigh", "max", "ultra"))
    assert all(low["runtime_ms"] > ultra["runtime_ms"]
               for low, ultra in zip(low_dag["repetitions"], ultra_dag["repetitions"]))
    summary = {
        "attempts": len(rows),
        "completed": sum(r["status"] == "completed" for r in rows),
        "main_accepted_completed": sum(r["main_accepted_and_completed"] for r in rows),
        "settings": len(effort_settings),
        "main_macro_100_settings": sum(r["main_macro"] == 100 for r in effort_settings),
        "exact_cost_attempts": sum(r["costs"]["api_usd_estimated"] is not None for r in rows),
        "mvcc_main_accepted_completed": sum(r["accepted"] for r in mvcc),
        "mvcc_available_test_results": sum(r["usable"] for r in mvcc),
        "mvcc_no_added_tests": sum(r["status"] == "no_added_tests" for s in mvcc for r in s["repetitions"]),
        "mvcc_status_counts": dict(Counter(r["status"] for s in mvcc for r in s["repetitions"])),
        "mvcc_sol61_low_medium": {
            "low_mean_usd": low_mvcc["mean_usd"],
            "medium_mean_usd": medium_mvcc["mean_usd"],
            "cost_increase_pct": (medium_mvcc["mean_usd"] / low_mvcc["mean_usd"] - 1) * 100,
            "low_detected_out_of_12": sum(r["killed_count"] for r in low_mvcc["repetitions"]),
            "medium_detected_out_of_12": sum(r["killed_count"] for r in medium_mvcc["repetitions"]),
        },
        "dag_sol61_low_ultra": {
            "cost_multiple": ultra_dag["mean_usd"] / low_dag["mean_usd"],
            "low_mean_runtime_ms": low_dag["mean_runtime_ms"],
            "ultra_mean_runtime_ms": ultra_dag["mean_runtime_ms"],
            "runtime_reduction_pct": (1 - ultra_dag["mean_runtime_ms"] / low_dag["mean_runtime_ms"]) * 100,
            "low_mean_peak_kib": low_dag["mean_peak_kib"],
            "ultra_mean_peak_kib": ultra_dag["mean_peak_kib"],
            "python_allocation_reduction_pct": (1 - ultra_dag["mean_peak_kib"] / low_dag["mean_peak_kib"]) * 100,
        },
        "sol61_max_low": {
            "cost_multiple": max_all["cost_multiple"],
            "low_main_macro": low_all["main_macro"], "max_main_macro": max_all["main_macro"],
            "low_accepted": low_all["accepted"], "max_accepted": max_all["accepted"],
        },
    }
    assert summary["attempts"] == 216 and summary["main_accepted_completed"] == 213
    assert summary["main_macro_100_settings"] == 16 and summary["mvcc_main_accepted_completed"] == 36
    assert summary["mvcc_available_test_results"] == 35
    return {
        "schema_version": 1,
        "source_sha256": {relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() for relative in SOURCES},
        "models": models, "efforts": efforts,
        "mvcc": {"task": "06-transactions", "defects": [{"id": defect, "label": label} for defect, label in DEFECTS],
                 "settings": mvcc, "pair_mean_policy": "Only 2/2 available scores produce a pair mean. No added tests is explicit zero; unavailable is null.",
                 "score_definition": "Executed assertion failures against six fixed single-defect controls, after candidate and reference tests pass without skips. Errors and hangs are unavailable, not detections."},
        "dag": {"task": "03-hard", "settings": dag,
                "runtime_definition": "Each candidate: median of three archived samples. Setting: mean of the two candidate medians.",
                "memory_definition": "Mean of two candidate tracemalloc peaks in KiB; Python allocations, not process RSS or native memory.",
                "workload": "1,500-task dependency chain; three worker limit"},
        "effort": {"settings": effort_settings,
                   "cost_definition": "Mean API-equivalent USD across all 12 attempts. Partial totals have null exact mean and an observed lower bound.",
                   "quality_definition": "Equal six-task macro average of paired main snapshot functional grades. Completed acceptance is separate.",
                   "multiples_reference": "The same model at Low effort."},
        "repetition_cost": repeat_cost, "pricing_bridge": pricing_bridge, "summary": summary,
    }


def encoded(insights):
    return (json.dumps(insights, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def write(destination=None):
    destination = Path(destination) if destination is not None else ROOT / "results/insights.json"
    destination.write_bytes(encoded(derive()))
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify the checked-in view without rewriting it.")
    args = parser.parse_args()
    insights = derive()
    destination = ROOT / "results/insights.json"
    if args.check:
        assert destination.read_bytes() == encoded(insights), "results/insights.json differs from frozen evidence."
    else:
        destination.write_bytes(encoded(insights))
    print(json.dumps({"verified": True, "written": not args.check,
                      "mvcc_settings": len(insights["mvcc"]["settings"]),
                      "dag_settings": len(insights["dag"]["settings"]),
                      "exact_repeat_pairs": insights["repetition_cost"]["exact_pairs"],
                      "matched_price_attempts": insights["pricing_bridge"]["matched_n"]}, indent=2))


if __name__ == "__main__":
    main()
