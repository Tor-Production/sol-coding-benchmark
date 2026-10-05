# Scoring and code-quality evidence

Code quality is reported as several evidence layers. Passing hidden tests, completing a delivery, writing effective tests and producing maintainable code answer different questions. The archive does not manufacture a single full quality score while manual review remains pending.

| Layer | Scope | Status and interpretation |
| --- | --- | --- |
| Main functional score | Six tasks, all 216 snapshots | Fixed observable contract behavior |
| Accepted delivery | All 216 attempts | Main acceptance plus a completed agent turn |
| Expanded correctness | Original four tasks | Post-hoc properties, boundaries and concurrency evidence |
| Candidate-test sensitivity | All six tasks | Detection of fixed, task-specific defects; coverage disclosed |
| Generated-code runtime/allocation | Original four tasks | Descriptive, unscored workloads |
| Manual design review | Pending | Source-based clarity, design and failure-maintenance assessment |

See the [results](results.md) for the scores and [methodology](methodology.md) for cost/time accounting.

## Main functional score and acceptance

For the original four tasks, the score is `100 × passed scored hidden checks / scored hidden checks`. Public tests and protected-file integrity are additional acceptance gates; they do not contribute extra percentage points. A subcase failure fails its containing check.

For the two complex tasks, the score is the weighted sum of category percentages below. The evaluator retains every check's outcome and diagnostic detail. **Main code acceptance** requires every scored hidden check, every supplied public test and intact protected files. **Accepted delivery** additionally requires `status == completed`; invalid/mismatched attempts do not become comparable deliveries.

A timeout can therefore leave a 100-point saved snapshot without being accepted as a finished delivery. Conversely, a completed turn can deliver code that fails a scale check. In this archive, 214 turns completed and 213 deliveries were accepted. The provider-failed interval scaffold and the passing snapshot from the reservation timeout are preserved.

### One fixed ambiguity policy

`Service.test_retry_after_release` is excluded from the reservation main score and acceptance for every model/effort, including inherited observations. The original contract's “ORIGINAL” record wording leaves the returned status after release ambiguous. This harmonized policy is disclosed rather than described as a preregistered initial-run rule. The unchanged test remains a diagnostic; raw scores and raw acceptance remain available. Expanded evaluation accepts either active or released status for the retry response while still requiring unchanged identity and stock. This policy does not waive idempotency or inventory integrity.

## Complex-task category weights

| Optimizer category | Weight | Checks |
| --- | ---: | ---: |
| Validation and input integrity | 20% | 3 |
| Dependencies, exclusions and budgets | 25% | 3 |
| Exact optimality and tie rules | 35% | 3 |
| Structured scale | 20% | 3 |

| MVCC category | Weight | Checks |
| --- | ---: | ---: |
| Snapshots, own writes, savepoints and copies | 25% | 4 |
| Serializable isolation and histories | 35% | 7 |
| Recovery, integrity and replay | 25% | 4 |
| Validation, lifecycle and atomicity | 15% | 4 |

```text
category_score = 100 × passed_checks / category_checks
task_score = Σ(category_weight × category_score) / 100
setting_score = mean_over_six_tasks(mean_of_two_repetitions(task_score))
```

Each task contributes one sixth to the setting score. Larger test suites do not get extra weight. Both repetitions are required for each task before publishing a complete setting mean. The optimizer's single failed scale check produces 93.333333 points for that attempt, 96.666667 for its paired task mean and approximately 99.444444 for Sol 6 / Low's six-task macro score.

## Expanded correctness for the original four tasks

The preserved quality-v2.1 evaluator adds randomized/model-based properties, boundary validation and concurrency checks. Their category weights are **40% properties, 30% boundaries, 30% concurrency**. Categories absent by task design are omitted and the remaining weights renormalized. An applicable failed test is a failure; an incomplete evaluator run cannot silently become a complete score.

This evaluation was added after the original candidates were generated. It is exploratory evidence, not a preregistered historical model ranking, and the agents did not receive these checks or feedback. Completed-code reporting excludes the provider-failed scaffold and noncompleted reservation snapshot from completed-code means, while retaining their raw evaluation records. The completed solutions passed 920 of 922 expanded check methods; randomized subcases are not counted as independent methods in that denominator.

Two Low-effort cache solutions fail the valid huge-integer TTL case described in [tasks](tasks.md#02--ttl-cache-with-lru-eviction). Their main scores remain unchanged. The complex tasks instead use their predeclared behavioral categories; expanded correctness is not invented for them or substituted into the old formula.

## Do candidate-added tests detect defects?

Test strength is mutation sensitivity on a **fixed per-task defect set**. The evaluator recognizes `unittest`-compatible `tests/test*.py` files and excludes the supplied `test_public.py`. Candidate-added tests must run without skips and pass on both the saved candidate and a correct positive control before sensitivity is usable. A test that depends on private candidate internals may be helpful locally but unsuitable for a cross-implementation mutation measurement.

| Task | Fixed mutants |
| --- | ---: |
| Interval union | 4 |
| TTL/LRU cache | 5 |
| Concurrent DAG | 5 |
| Reservation service | 6 |
| Exact optimizer | 5 |
| MVCC/recovery | 6 |

```text
test_strength = 100 × detected_mutants / fixed_mutants
```

The two preserved evaluators differ in their detection rules; do not treat their percentages as a universal interchangeable test-quality scale:

- **Original four tasks, quality-v2.1:** a test that passed on the positive control detects a mutant if that same test becomes a recorded failure or runtime error. Loader failures, harness errors and timeouts do not count as kills and block a complete mutation score.
- **Optimizer and MVCC:** only executed assertion failures count as kills. Import/execution errors, skipped tests and hangs make the mutation result inconclusive/unavailable. Each candidate/reference/mutant probe has a 30-second limit.

No recognized added tests produces **zero for test strength only**, without reducing main correctness. Reference incompatibility, import problems, timeouts or inconclusive mutant execution produce **N/A**, not synthetic zero. Usable scores of zero and unavailable scores must stay distinct.

In the archive, 175 of 214 completed candidates have usable sensitivity scores; 29 completed candidates have no recognized added tests. For the complex tasks, 55 of 72 scores are usable, including ten no-added-test zeros. Sixteen optimizer results are unavailable: twelve positive-control runs hit the 30-second limit, three have import/execution issues and one has inconclusive mutant execution. One MVCC result is unavailable because its candidate-added tests fail on the reference. These limitations, particularly optimizer reference timeouts, do not establish a candidate implementation defect.

A model/effort/task pair mean requires both usable scores; otherwise the results display N/A with coverage. Read the per-task coverage before comparing models. High sensitivity demonstrates detection of these seeded defects, not comprehensive test coverage or freedom from other bugs.

## Generated-code runtime and allocation

The original four tasks use one warm-up and the median of three sequential execution runs on their fixed workloads: 30,000 intervals; cache capacity 1,000 with 1,000 writes and 3,000 reads; 1,500 DAG nodes; and 40 reservation cycles. A separate `tracemalloc` run measures peak Python allocations. This does not measure process RSS, native/SQLite memory or agent generation time. Runtime and allocation are unscored and should be interpreted within each task, with scheduling noise in mind.

No equivalent runtime/allocation profile was collected for the two complex tasks. Their scale and trace checks are functional evidence, not a substitute runtime leaderboard.

## Pending manual review

The retained rubric asks a reviewer to score each of three criteria from 0 to 4, with reviewer identity, source path/line and an explanation. Model labels should be blinded where possible.

| Criterion | What a 4 requires |
| --- | --- |
| Clarity | Core invariants, state transitions and boundaries are easy to trace without redundant explanation |
| Design | Cohesive responsibilities and explicit state ownership support a concrete likely change without needless machinery |
| Failure maintenance | Validation, errors, cleanup and recovery are easy to verify; commands and test evidence support maintenance |

A concise easy solution can merit the highest rating. Line count, annotations, documentation count and abstraction count are review signals, not mechanical grades. Existing scaffold documentation is not credited as generated work. No manual scores have been supplied, so no full manual or overall code-quality ranking is claimed.

The original four-task composite keeps its historical weights: **30% main correctness, 35% expanded correctness, 20% test strength and 15% manual review**. An automatic-evidence score may normalize the first three components over 85 points only when all three are present and valid. It is **not** a full quality score. That formula is preserved for the original tasks and is not extrapolated to the optimizer/MVCC tasks.
