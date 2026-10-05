# Tasks and evaluation design

The six tasks cover increasing implementation and invariant complexity. Difficulty labels describe the intended workload, not an empirically calibrated universal scale. Every model sees the same contract and scaffold for a task. There are 36 archived attempts per task. Their main functional scores contribute equally to the primary **USD-versus-functional-quality** comparison: average the two repetitions per task, then the six task means. Task time and other quality evidence remain separate.

| Task | Main challenge | Agent limit | Contract |
| --- | --- | ---: | --- |
| 01 — Interval union | Correct boundaries, validation and efficient pure transformation | 10 min | [TASK.md](../tasks/01-easy/TASK.md) |
| 02 — TTL/LRU cache | Interacting expiry and recency state | 20 min | [TASK.md](../tasks/02-medium/TASK.md) |
| 03 — Concurrent DAG | Whole-graph validation, concurrency and failure propagation | 40 min | [TASK.md](../tasks/03-hard/TASK.md) |
| 04 — Reservation service | SQLite, HTTP and CLI sharing atomic persistent state | 40 min | [TASK.md](../tasks/04-components/TASK.md) |
| 05 — Exact optimizer | Exact search under dependencies, conflicts, budgets and ties | 40 min | [TASK.md](../tasks/05-optimizer/TASK.md) |
| 06 — MVCC with recovery | Consistent snapshots, conflict history, savepoints and replay | 40 min | [TASK.md](../tasks/06-transactions/TASK.md) |

## 01 — Merge inclusive integer intervals

Implement `merge_intervals(ranges)` without mutating its input. It must merge overlap, duplicates, containment and **adjacent** integer ranges, return sorted list pairs, reject malformed containers and booleans, and support negative and arbitrarily large integers. The contract targets O(n log n) time and O(n) space, including 30,000 intervals.

Main checks include shape/endpoint validation, purity, boundary cases, a seeded union oracle and a large input. Expanded evaluation adds 1,200 randomized union cases and further boundary/purity checks. This task tests precise implementation with a familiar algorithm; passing it provides little evidence about broad reasoning superiority.

## 02 — TTL cache with LRU eviction

Implement `TTLCache(capacity, ttl, clock)` with an injected clock. Reads update recency without extending TTL; writes reset expiry; expiry occurs at `now >= expires_at`. Expired entries must be removed before they can evict live entries, even when an expired entry is recently used. Stored `None`, delete return values, independent instances and strict constructor validation are part of the contract. Thread safety and clock rollback are outside its scope.

Main checks target interactions between expiration and LRU state. Expanded checks use state-model traces and numerical boundaries. They found two completed Low-effort solutions that reject a valid positive integer TTL of `10**400` with an integer clock: `math.isfinite` attempts float conversion and raises `OverflowError`. The original main tests passed on both solutions. [Results](results.md) retains that distinction.

## 03 — Concurrent dependency graph

Implement `run_graph(tasks, max_workers=2)` using standard-library threads. Validate the entire graph before any user function runs. Each task executes at most once, after every dependency succeeds; a failure skips transitive descendants while unrelated work continues. Newly ready work must start when a worker is available, without waiting for an unrelated slow function. Output order is deterministic, inputs remain unchanged, and a 1,500-node chain cannot depend on Python recursion depth.

The tests use dependency/failure graphs, barriers and worker-count observations to check concurrency behavior. Cancellation and `BaseException` handling are excluded by the contract. This is a specified executor, not a general orchestration-platform assessment.

## 04 — Inventory reservation service

Implement the `reservation` package across a SQLite store, a JSON HTTP server and a CLI. The interfaces share schema and persistent stock state. Reservations must be atomic, cannot oversell under separate concurrent store instances, and have idempotency keys. Release restores stock once. Validation, rollback, HTTP status/error bodies, URL-encoded SKU handling, 64-KiB request limits and CLI exit codes are observable requirements.

Main checks exercise store operations, persistence, concurrency, HTTP and CLI. Expanded evaluation adds stock-model steps, races, persistence and error cases. The ambiguous wording about returning the “ORIGINAL” reservation on retry after release produced one frozen diagnostic exclusion: `Service.test_retry_after_release` is retained in raw evidence but excluded from main acceptance and the main score for every model/effort. Reservation identity and stock integrity remain required; [scoring](scoring.md) explains the policy.

## 05 — Exact constrained portfolio optimizer

Implement `solve(projects, budget, required=())` for up to 32 projects and one to three resource budgets. Projects have integer values, nonnegative costs, transitive DAG prerequisites and potentially one-sided exclusions. Values may be negative; mandatory projects can make an instance infeasible. Validate all input, including projects that can never fit, without mutating nested structures.

The result must be **globally optimal**. Feasible subsets are ordered by greatest value, then lexicographically smallest cost tuple, then lexicographically smallest sorted ID tuple. Greedy approximations and plausible but incorrect ties do not satisfy the contract.

The evaluator's exhaustive oracle enumerates small instances independently of the branch-and-bound positive control. It covers 64 fixed-seed arbitrary problems per attempt, feasible/infeasible mandatory sets and greedy traps. Three structured 32-project fixtures assess bounded-search behavior:

1. Thirty-one affordable positive projects plus an individually unaffordable high-value decoy.
2. A negative-value dependency chain.
3. Pair conflicts under two budgets.

Each scale fixture runs in an isolated process with an 8-second limit. These fixtures do not establish polynomial-time performance on arbitrary NP-hard inputs. The [category weights](scoring.md#complex-task-category-weights) distinguish validation, constraints, optimality and scale.

All small oracle cases passed in the archived experiment. Sol 6 / Low repetition 1 exceeded the scale limit on the first fixture; its task score is 93.33/100. A separate bounded replay reproduced that timeout, while the positive control and the paired Low solution finished in approximately 0.20 and 0.22 seconds. The replay was diagnostic and did not replace the original attempt or grade.

## 06 — Serializable MVCC with savepoints and recovery

Implement an in-memory `Engine` and transactions with snapshot reads plus pending writes. Calls are sequential, but transactions can remain alive and interleave. This removes thread/network/storage setup while retaining difficult state interactions.

The contract explicitly specifies conservative serializable optimistic validation: any committed write after a transaction's snapshot conflicts when it touches a read key, staged-write key or scanned prefix. Missing-key reads and empty ranges count. Read-only commits must validate. Same-value writes, delete/reinsert and ABA histories still conflict even when present values look unchanged. Unrelated writes may progress.

Nested savepoints roll back pending writes while retaining all read dependencies. Checkpoints, logs and outputs are defensive copies. Recovery validates the whole checkpoint/log before acceptance, requires contiguous versions and sorted unique writes, and retains a base version for subsequent log requests. Recovery here means logical replay of exported data, not physical disk or `fsync` durability.

The evaluator includes write skew, blind-write conflicts, phantoms, absent reads, ABA, savepoint read retention, failed-operation atomicity, a 480-step seeded mixed-interleaving oracle, a 180-transaction replay trace and checkpoint/log-prefix round trips. All MVCC main checks passed in the archive. Candidate tests provide additional, separately scored evidence; at High, Xhigh, Max and Ultra, both Sol 6.1 repetitions detected all six fixed MVCC mutants.

## Controls and interpretation

Before inference, the main evaluator was qualified using empty scaffold implementations, positive controls and deliberate defective implementations. The complex suite also required detection of five optimizer and six MVCC contract mutants. Qualification establishes that these checks detect the declared defects; it does not prove that the grader is exhaustive.

The optimizer and MVCC contracts request algorithm/invariant explanations. Those explanations are review material, not automatically scored reasoning evidence. Observable behavior, candidate-test strength, execution efficiency and pending manual review remain separate. Consult [limitations](limitations.md) for what these task results can support.
