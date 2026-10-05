# Reproduce and inspect the benchmark

There are three distinct activities: verify the published evidence offline, replay checks on archived code, and run a **new** model experiment. Reading and verification do not require a Codex account or paid inference. A fresh run produces new evidence rather than replacing the archive.

## 1. Verify the public archive

From the repository root:

```powershell
python scripts/verify_archive.py
```

The verifier checks the exported comparison, candidate identities and publication manifest. Consult [provenance](provenance.md) for the original-source hashes versus the hashes of relocated public exports. Inspect a result by run ID in [results/results.json](../results/results.json), then open its directory in [candidates/](../candidates/). The [web results](results.md) and [PDF](../artifacts/Sol_Benchmark_Consolidated_EN.pdf) provide a readable entry point.

## 2. Verify the stand without model calls

The harness uses Node's built-in modules and Python's standard library; it has no npm runtime dependencies. Install Node and Python, then run:

```powershell
npm test
```

The direct equivalent is `node scripts/validate_stand.mjs`. This stages the stand in a temporary directory and exercises evaluator qualification, telemetry arithmetic, the full matrix, RPC behavior and a fake-server integration path. It verifies empty stubs, positive controls and deliberately defective implementations, including the eleven complex-task mutants. It does not submit a model turn, use authentication or contact a model service.

Historical collection used **Node.js 22.18.0, Python 3.14.0, Codex CLI 0.159.0 and PowerShell 7 on Windows**. Matching that environment improves comparison; a new runtime remains a new experiment and must be recorded. Elapsed time, generated-code execution time and allocation can differ by machine even when behavior matches.

## 3. Inspect public tests on one archived candidate

The saved snapshots are review evidence. Execute tests in a separate copy so that temporary test artifacts do not contaminate the archived directory. For example, in PowerShell:

```powershell
$benchRunId = '06-transactions-6.1-sol-high-r1'
$benchReplay = Join-Path ([System.IO.Path]::GetTempPath()) ('sol-replay-' + [guid]::NewGuid())
Copy-Item -LiteralPath (Join-Path 'candidates' $benchRunId) -Destination $benchReplay -Recurse
Push-Location $benchReplay
try {
    python -B -m unittest discover -s tests -p test_public.py -v
} finally {
    Pop-Location
}
```

This runs only the supplied public tests. To replay the **main** evaluator, including the frozen ambiguity policy and protected-file checks, use:

```powershell
node harness/replay.mjs 06-transactions-6.1-sol-high-r1
```

The replay verifies the archived candidate manifest, copies it to temporary staging, and writes new evaluation evidence under `analysis/replay/`. It needs Node/Python but no Codex authentication or inference. The result reports the original delivery status separately: replaying a passing timeout snapshot does not convert the original attempt into a completed delivery. Its duration is a new local measurement, not the archived agent time.

The original four-task supplementary evaluator can also be replayed offline:

```powershell
python -B quality-v2.1/run.py qualify
python -B quality-v2.1/run.py freeze
python -B quality-v2.1/run.py evaluate --run-id 02-medium-6-sol-low-r1
python -B quality-v2.1/run.py report
```

It reads the archive and writes under `analysis/quality-v2.1/`. Omit `--run-id` to evaluate all 144 archived original-task candidates; this can take substantial local time but makes no model calls. An optional `--output <new-local-dir>` selects another output directory; use the same directory throughout qualification, freeze, evaluation and reporting. It preserves the historical expanded-check and mutation rules. Fresh runtime/memory measurements can differ from the archive's machine measurements.

For one archived complex-task candidate's added-test sensitivity:

```powershell
python -B evaluator/test_strength.py --archive --run-id 06-transactions-6.1-sol-high-r1
```

This writes separate replay evidence under `analysis/complex-test-strength-replay/`. None of these replay commands overwrite checked-in results or candidate snapshots. Read [scoring](scoring.md) for N/A rules and the distinction between the two mutation evaluators.

## 4. Prepare a fresh six-task experiment

Use a new checkout with no measured `runs/` or existing experiment freeze. The checked-in archive is read-only input for inspection. A fresh schedule contains **216 paid attempts**, including the four original tasks, rather than importing their historical scores.

On Windows, use PowerShell 7 with `node`, `python` and `codex` on PATH. An authenticated Codex home is needed for native preflight and measured inference. Explicit executable/home overrides are available when PATH does not identify the intended installation:

```powershell
$env:BENCH_CODEX_EXE = 'C:\path\to\codex.exe'
$env:BENCH_PYTHON_EXE = 'C:\path\to\python.exe'
$env:BENCH_PWSH_EXE = 'C:\path\to\pwsh.exe'
$env:BENCH_AUTH_HOME = 'C:\path\to\authenticated-codex-home'
```

Replace these example paths with the real local installation. Do not put credentials or an authenticated home in the repository. Before starting a new measured experiment, choose a distinct `experiment_id` in `config/experiment.json`. Keep the same task contracts, two repetitions and frozen price schedule if comparing to the archive. Any changes to tasks, weights, prices or environment must be disclosed.

```powershell
.\Run-Benchmark.ps1 -Action Prepare
.\Run-Benchmark.ps1 -Action Preflight
.\Run-Benchmark.ps1 -Action Status
```

`Prepare` qualifies the evaluators, writes the full schedule and freezes the actual local inputs/runtime. `Preflight` checks native model availability and all eighteen requested model/effort settings without `turn/start`; it does not submit inference. It checks the exact model, effort, Standard tier and effective permission settings. If a model or effort is unavailable, do not silently substitute another configuration.

## 5. Start, inspect and resume paid inference

The execution gate is explicit:

```powershell
.\Run-Benchmark.ps1 -Action RunAll -Execute
```

This performs sequential, credit-consuming inference on the fresh 216-attempt schedule. To run one effort across the full suite instead, select it:

```powershell
.\Run-Benchmark.ps1 -Action RunAll -Effort low -Execute
```

That schedules 36 attempts: three models × six tasks × two repetitions. All six efforts must eventually be covered for a complete comparable experiment.

Inspect progress and export fresh results with:

```powershell
.\Run-Benchmark.ps1 -Action Status
.\Run-Benchmark.ps1 -Action Report
```

Fresh execution stores snapshots, manifests and result records under `runs/`; live workspaces are under `workspaces/`. `Report` writes fresh JSON/CSV/English summaries under `analysis/`, separate from the published historical `results/`. It does not recreate the archived PDF.

Calling `RunAll` again preserves terminal attempts and continues pending ones. It can finish missing grading for a saved terminal snapshot, but **does not retry failed model inference**. An attempt still marked running/initializing or a protocol mismatch requires investigation before more spending. A run lock prevents concurrent harness controllers. Do not delete a lock until its recorded process has stopped. Use a new experiment to conduct a deliberate rerun, and retain the original failure record.

## 6. Supplementary quality evaluation

The complex candidate-test metric can be evaluated after fresh snapshots have been graded:

```powershell
.\Run-Test-Strength.ps1
```

It covers only optimizer/MVCC candidates, verifies snapshot/freeze integrity and grades copies under `analysis/complex-test-strength/`. The original four-task quality-v2.1 adapter described above replays the historical archive; it should not be presented as an all-six-task supplementary evaluator for fresh runs. Neither evaluator creates a full code-quality score without valid manual review; runtime/memory remain unscored.

The Node CLI equivalents for the main workflow are:

```text
node harness/cli.mjs prepare
node harness/cli.mjs preflight
node harness/cli.mjs status
node harness/cli.mjs runall --execute
node harness/cli.mjs report
```

This portable entry point does not imply that non-Windows measured execution has been validated to reproduce the historical environment. Preserve the new freeze, runtime versions, all outcomes and unavailable metrics when sharing a rerun.

## 7. Regenerate the results page and charts

The readable results page and SVG figures are derived from the published archive. Regenerate them with the Python standard library:

```powershell
python scripts/analyze_results.py
```

This validates 216 unique attempts, the full comparison matrix, twelve observations per setting, acceptance, macro scores, elapsed-time means and cost coverage before writing `docs/results.md` and the SVG figures under `assets/`. It makes no model calls and does not change grades, candidates, archived input JSON or the PDF.

Optional PNG previews require the report dependencies:

```powershell
python -m pip install -r requirements-report.txt
python scripts/analyze_results.py --png
```

The primary `cost-quality` figure has **USD per attempt on X** and the **six-task main functional macro score out of 100 on Y**, with a full-range view and a labeled ceiling zoom. `cost-acceptance` uses completed-delivery percentage as a separate criterion. Effort curves show cost and main functional score; `effort-time` is secondary. Candidate-test sensitivity remains a separate figure with explicit unavailable coverage.

SVG geometry is deterministic and independent of third-party libraries; PNG rasterization uses local fonts and can vary slightly across platforms. Preserve the original denominators and grades, connected effort settings, and right-pointing partial-cost lower-bound arrows when changing the presentation.

## 8. Rebuild the current English PDF

Install the report dependencies and build the report from the published archive:

```powershell
python -m pip install -r requirements-report.txt
python scripts/build_report.py
```

The builder writes `output/pdf/Sol_Benchmark_Consolidated_EN.pdf`, leaving the published artifact untouched. It reads the repository's results and provenance and draws PDF charts directly with ReportLab; PNG generation is unnecessary. It does not access Codex authentication, submit inference, run the benchmark or change candidate snapshots and scores. The published [current report](../artifacts/Sol_Benchmark_Consolidated_EN.pdf) and [original 4 October report](../artifacts/archive/Sol_Benchmark_Consolidated_EN_20261004.pdf) remain available for comparison.

ReportLab and pypdf are required; Pillow is used for optional PNG previews. The builder uses Windows Segoe UI fonts when available and falls back to core Helvetica fonts on other systems.

This rebuild reproduces the report's analysis and layout rather than guaranteeing identical PDF bytes across fonts and environments. Render the result and inspect all pages before replacing the published artifact, then update the publication provenance and archive manifest. Replacing only the published PDF without its matching provenance will correctly fail archive verification. [Provenance](provenance.md#current-presentation-revision) explains the current revision and original-report preservation.
