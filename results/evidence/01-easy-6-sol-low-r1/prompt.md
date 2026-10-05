# Merge inclusive integer intervals

Implement `merge_intervals(ranges)` in `intervals.py` using Python's standard
library. `ranges` is a list or tuple of two-element lists/tuples `[start, end]`.
Endpoints must be integers (booleans are invalid) and `start <= end`.
Raise `ValueError` for malformed input, including an invalid outer container.

Return a list of `[start, end]` lists sorted by start. Merge overlapping,
contained, duplicate, and **adjacent** integer intervals: `[1, 2]` and `[3, 5]`
become `[1, 5]`. The empty input returns `[]`. Negative and arbitrarily large
integers must work. Do not mutate the input. Aim for O(n log n) time and O(n)
space, including on 30,000 intervals.

Run `python -m unittest discover -s tests -v`. You may add tests. Do not modify
the supplied tests or this contract. Finish with a concise account of the change
and tests run. No external dependencies, network access, or subagents.
