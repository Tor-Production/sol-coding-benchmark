# Concurrent dependency-graph executor

Implement `run_graph(tasks, max_workers=2)` in `dag.py`, with standard-library
threads. `tasks` maps nonempty string IDs to dictionaries containing `deps`
(a list of IDs) and `fn` (a zero-argument callable). Validate the ENTIRE graph
before running any function. Invalid task IDs, duplicate deps, unknown deps,
self-deps, cycles, malformed tasks, non-callable fn, and non-positive/non-int
max_workers (including bool) raise ValueError. Empty graph returns {}.

Return a dictionary inserted in sorted task-ID order, containing exactly:

- success: `{"status": "completed", "value": <returned value>}`
- failure: `{"status": "failed", "error": <str(exception)>}`
- blocked descendant: `{"status": "skipped"}`

Run a task only after ALL its dependencies complete successfully. Call each fn
at most once. Catch ordinary Exception failures without stopping unrelated work.
Skip transitive descendants of failures, even with multiple parents. Independent
ready functions must run concurrently up to max_workers. Do not wait for an
unrelated slow function before starting a newly ready dependent function when
a worker is free. Return only after all started work has finished. Do not mutate
tasks or dependency lists. Cancellation and BaseException handling are out of
scope. The implementation should handle a chain of 1,500 tasks without relying
on Python recursion depth.

Run `python -m unittest discover -s tests -v`. Add tests if useful, preserve
supplied tests and contract, and summarize your work. No external dependencies,
network access, or subagents.
