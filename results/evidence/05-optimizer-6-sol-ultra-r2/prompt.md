# Exact constrained portfolio optimizer

Implement `solve(projects, budget, required=())` in `optimizer.py` using only
the Python standard library. Return a globally optimal feasible subset, not
a heuristic approximation. This is an exact combinatorial optimization task.

`projects` is a list of at most 32 dictionaries, each with exactly these keys:

- `id`: unique nonempty string;
- `value`: integer, including zero or negative values; bool is invalid;
- `cost`: list of nonnegative integers, one per budget dimension; bool invalid;
- `requires`: list of distinct other project IDs that must also be selected;
- `excludes`: list of distinct other project IDs that cannot be selected together.

`budget` is a list of 1 to 3 nonnegative integers (bool invalid). `required`
is a list or tuple of distinct known IDs that must appear in the solution.
No self-reference or unknown ID is allowed. The dependency graph must be
acyclic. Exclusions may be declared on only one side; they still forbid the
pair in either direction. Requiring and excluding the same project is a valid
input that can make that selection infeasible. Extra/missing dictionary keys,
wrong container types, malformed values, duplicate IDs/references, cycles,
and invalid required IDs raise ValueError. Validate all projects even if some
can never fit the budget. Do not mutate any input, including nested lists.

A subset is feasible when it contains all required IDs and all transitive
dependencies of its members, contains no excluded pair, and its total cost
does not exceed any budget dimension. Among feasible subsets choose, in order:

1. Greatest sum of project values.
2. Lexicographically smallest total-cost tuple (dimension order is significant).
3. Lexicographically smallest tuple of selected IDs sorted in Python string order.

Return exactly `{"selected": [sorted IDs], "value": integer, "cost": [totals]}`.
If no subset is feasible, return None. Without required IDs the empty subset
is always feasible. An empty project list returns value 0 and zero costs.

Example: a has value 8, cost [4], requires b; b has value -1, cost [1];
c has value 6, cost [4], excludes b. Budget [5] chooses [a,b] with value 7
and cost [5]. Requiring c chooses [c]. Requiring both a and c returns None.

Handle small arbitrary instances exactly, and structured 32-project cases
without enumerating every one of the 2**32 subsets. Hidden scale checks use
a generous 8-second limit per fixture in an isolated evaluator process.
All cases obey this contract; NP-hard worst-case polynomial time is not required.

Run `python -m unittest discover -s tests -v`. Preserve supplied tests and
instructions. Add your own contract-based tests as `tests/test_*.py` if useful.
No external dependencies, network, or subagents. Briefly explain your algorithm,
optimality argument, and remaining worst-case complexity in the final response.
