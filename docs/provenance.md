# Provenance and archive integrity

The public repository separates **immutable measured evidence** from a **portable stand for fresh experiments**. The published result export contains 216 observations across all six tasks; it does not treat fresh local reruns as replacements for the recorded results.

## What to inspect

| Repository artifact | Purpose |
| --- | --- |
| [results/results.json](../results/results.json) | Unified 216 attempts, 18 setting summaries, frozen rates and scoring policy |
| [results/quality-v2.1.json](../results/quality-v2.1.json) | Original four tasks' 144 detailed supplementary evaluations and rubric |
| [results/complex-test-strength.json](../results/complex-test-strength.json) | The two complex tasks' 72 candidate-test evaluations |
| [results/candidate-manifests.json](../results/candidate-manifests.json) | Expected hashes for the files in each saved candidate |
| [candidates/](../candidates/) | Candidate snapshots, addressed by run ID |
| [results/report-audit.json](../results/report-audit.json) | Original report audit of settings, snapshots, turns, usage and arithmetic |
| [results/provenance.json](../results/provenance.json) | Source identities and publication transformations |
| [results/archive-manifest.json](../results/archive-manifest.json) | File hashes for the public archive |
| [artifacts/Sol_Benchmark_Consolidated_EN.pdf](../artifacts/Sol_Benchmark_Consolidated_EN.pdf) | Original consolidated English report |
| [scripts/verify_archive.py](../scripts/verify_archive.py) | Offline public-export verification |

The original four-task observations are inherited from the preserved initial/effort/Max–Ultra evidence, and the optimizer/MVCC collection contributes 72 observations. The unified export preserves all model IDs, task IDs, efforts, repetition IDs, scores and terminal outcomes. This source history is relevant to audit provenance; the analysis groups by model/effort/task rather than by collection stage.

## Original source identities

The consolidated report was prepared on **4 October 2026** in the Europe/Kiev client time context. Its original source SHA-256 values are:

| Source | Original SHA-256 |
| --- | --- |
| Consolidated result export | `efe52197a02f4c255733d7bd9911d9088fbde54204ee870ea0d36e51483fc740` |
| Four-task quality-v2.1 export | `beec8746f0ab9677df1f850738f2f5f3ddc147084435225309ff9998407477c0` |
| Complex candidate-test export | `c3b85878d924e78133d8a8cf1e735aee3f13deebd7015fdeb06eb4c5b783c4dd` |
| Complex experiment freeze | `4c311e2b4372e2c15786095dec3c09572e8b95245ea3b4334ade53e317fad4c5` |

These attest the **original local sources used for the report**. The public JSON files relocate machine-specific references and reorganize the original export into a unified structure. Their published file hashes therefore differ; use the public archive manifest to check downloaded files. Do not compare a sanitized public JSON's bytes to an original-source hash and expect equality. Candidate manifests separately verify the saved candidate file bytes.

Credentials, authenticated homes and private account/session machinery are not required to inspect results. The public data retains the experiment measurements and candidate code while using repository references for accessible evidence. The publication provenance records the transformation so that a reviewer can distinguish a path change from a changed score.

## What the original audit checked

The report audit verified all 216 attempt snapshots and the recorded requested/effective settings, single-turn inference evidence, token counters and frozen price arithmetic. It found 214 completed turns, 213 accepted deliveries and 214 exact USD estimates. It also checked the complete model/effort/task/repetition matrix and retained failure records. Automated command scans produced no flagged external-read attempts after excluding legitimate own-workspace paths.

This audit establishes consistency of the available evidence. It is not a security proof of read isolation and does not turn a passing test suite into exhaustive correctness. [Limitations](limitations.md) and [scoring](scoring.md) explain the remaining uncertainty and unavailable criteria.

## Fresh experiments

The repository's current `config/`, `tasks/`, `evaluator/` and `harness/` are the portable rerun implementation. They retain the task contracts, scoring rules and historical price schedule while replacing machine-specific paths and scheduling all six tasks afresh. A new `Prepare` operation qualifies the controls and hashes those local inputs into `preparation/freeze.json`. It does not reuse the original collection freeze as though the portable harness were byte-identical.

Fresh outputs go to `runs/`, `workspaces/` and `analysis/`; the checked-in `results/`, `candidates/` and report remain the historical archive. Preserve a new experiment ID and its freeze with every new result set. Do not overwrite old outcomes, replace a failed inference with a later success, or relabel historical scores as if the supplementary rubric were shown to those agents.

Use the [reproduction guide](reproduction.md) for offline verification, evaluator replay and a fresh paid run. Model catalog support and exact runtime availability must be checked at the time of a new run; this archive cannot guarantee either.
