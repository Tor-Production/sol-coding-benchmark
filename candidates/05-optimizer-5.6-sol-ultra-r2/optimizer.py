"""Exact constrained portfolio optimization.

The public entry point is :func:`solve`.  The implementation deliberately
uses integer arithmetic throughout; in particular, neither bounds nor tie
breaking depend on floating point rounding.
"""

from bisect import bisect_left, bisect_right
from functools import cmp_to_key


_KEYS = {"id", "value", "cost", "requires", "excludes"}


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _validate(projects, budget, required):
    """Validate and copy the input into immutable, ID-sorted records."""
    if not isinstance(projects, list) or len(projects) > 32:
        raise ValueError("projects must be a list containing at most 32 items")
    if not isinstance(budget, list) or not 1 <= len(budget) <= 3:
        raise ValueError("budget must be a list with one to three dimensions")
    if any(not _is_int(x) or x < 0 for x in budget):
        raise ValueError("budget entries must be nonnegative integers")
    if not isinstance(required, (list, tuple)):
        raise ValueError("required must be a list or tuple")

    copied = []
    seen_ids = set()
    dimensions = len(budget)
    for project in projects:
        if not isinstance(project, dict) or set(project) != _KEYS:
            raise ValueError("each project must be a dictionary with exact keys")
        project_id = project["id"]
        value = project["value"]
        cost = project["cost"]
        requires = project["requires"]
        excludes = project["excludes"]
        if not isinstance(project_id, str) or not project_id:
            raise ValueError("project IDs must be nonempty strings")
        if project_id in seen_ids:
            raise ValueError("project IDs must be unique")
        seen_ids.add(project_id)
        if not _is_int(value):
            raise ValueError("project values must be integers")
        if not isinstance(cost, list) or len(cost) != dimensions:
            raise ValueError("project costs must match the budget dimensions")
        if any(not _is_int(x) or x < 0 for x in cost):
            raise ValueError("project costs must be nonnegative integers")
        if not isinstance(requires, list) or not isinstance(excludes, list):
            raise ValueError("requires and excludes must be lists")
        # Check duplicate references without assuming that malformed entries
        # are hashable.
        for references, label in ((requires, "requires"), (excludes, "excludes")):
            if any(not isinstance(x, str) for x in references):
                raise ValueError("%s entries must be project IDs" % label)
            if len(set(references)) != len(references):
                raise ValueError("project references must be distinct")
            if project_id in references:
                raise ValueError("projects may not refer to themselves")
        copied.append(
            (project_id, value, tuple(cost), tuple(requires), tuple(excludes))
        )

    for _project_id, _value, _cost, requires, excludes in copied:
        if any(x not in seen_ids for x in requires + excludes):
            raise ValueError("all project references must name known IDs")

    required_seen = set()
    required_copy = []
    for project_id in required:
        if not isinstance(project_id, str) or project_id not in seen_ids:
            raise ValueError("required contains an unknown project ID")
        if project_id in required_seen:
            raise ValueError("required project IDs must be distinct")
        required_seen.add(project_id)
        required_copy.append(project_id)

    copied.sort(key=lambda item: item[0])
    return copied, tuple(budget), tuple(required_copy)


class _Problem:
    """Validated bit-mask representation and exact search helpers."""

    def __init__(self, records, budget):
        self.ids = tuple(record[0] for record in records)
        self.n = len(records)
        self.full = (1 << self.n) - 1
        self.budget = budget
        self.dimensions = len(budget)
        self.index = {project_id: i for i, project_id in enumerate(self.ids)}
        self.values = tuple(record[1] for record in records)
        self.costs = tuple(record[2] for record in records)

        direct = []
        conflicts = [0] * self.n
        for i, record in enumerate(records):
            requires = 0
            for project_id in record[3]:
                requires |= 1 << self.index[project_id]
            direct.append(requires)
            for project_id in record[4]:
                j = self.index[project_id]
                conflicts[i] |= 1 << j
                conflicts[j] |= 1 << i
        self.direct = tuple(direct)
        self.conflicts = tuple(conflicts)

        # DFS both detects cycles and builds transitive dependency closures.
        closure = [0] * self.n
        color = [0] * self.n

        def visit(i):
            if color[i] == 1:
                raise ValueError("the dependency graph must be acyclic")
            if color[i] == 2:
                return closure[i]
            color[i] = 1
            result = 1 << i
            todo = direct[i]
            while todo:
                bit = todo & -todo
                todo ^= bit
                result |= visit(bit.bit_length() - 1)
            closure[i] = result
            color[i] = 2
            return result

        for i in range(self.n):
            visit(i)
        self.closure = tuple(closure)

        reverse = [0] * self.n
        for i, mask in enumerate(closure):
            todo = mask
            while todo:
                bit = todo & -todo
                todo ^= bit
                reverse[bit.bit_length() - 1] |= 1 << i
        self.reverse = tuple(reverse)

        # Mixed-radix cost ranks make (value, lexicographically minimal cost)
        # a single additive integer objective.  Every feasible cost rank is
        # smaller than objective_base.
        weights = [1] * self.dimensions
        product = 1
        for d in range(self.dimensions - 1, -1, -1):
            weights[d] = product
            product *= budget[d] + 1
        self.cost_weights = tuple(weights)
        self.objective_base = product
        scores = []
        for value, cost in zip(self.values, self.costs):
            penalty = sum(cost[d] * weights[d] for d in range(self.dimensions))
            scores.append(value * product - penalty)
        self.scores = tuple(scores)
        positive = 0
        nonnegative = 0
        for i, score in enumerate(scores):
            if score > 0:
                positive |= 1 << i
            if score >= 0:
                nonnegative |= 1 << i
        self.positive_mask = positive
        self.nonnegative_mask = nonnegative

        # Selecting i forbids not just each conflicting project, but every
        # project that transitively depends on such a project.
        select_ban = [0] * self.n
        for i in range(self.n):
            todo = conflicts[i]
            mask = 0
            while todo:
                bit = todo & -todo
                todo ^= bit
                mask |= reverse[bit.bit_length() - 1]
            select_ban[i] = mask
        self.select_ban = tuple(select_ban)

        # Projects whose own closure is contradictory or cannot fit at all
        # are unavailable, as are all projects depending on them.
        bad = 0
        for i, mask in enumerate(closure):
            if not self._conflict_free(mask):
                bad |= 1 << i
                continue
            totals = self._mask_cost(mask)
            if any(totals[d] > budget[d] for d in range(self.dimensions)):
                bad |= 1 << i
        base_unavailable = 0
        todo = bad
        while todo:
            bit = todo & -todo
            todo ^= bit
            base_unavailable |= reverse[bit.bit_length() - 1]
        self.base_unavailable = base_unavailable

        # Exact ratio orders used by the one-dimensional LP relaxations in
        # the branch-and-bound upper bound.
        ratio_orders = []
        for d in range(self.dimensions):
            candidates = [i for i in range(self.n) if scores[i] > 0]

            def compare(i, j, dimension=d):
                ci = self.costs[i][dimension]
                cj = self.costs[j][dimension]
                if ci == 0 or cj == 0:
                    if ci == cj:
                        if scores[i] != scores[j]:
                            return -1 if scores[i] > scores[j] else 1
                        return -1 if i < j else (1 if i > j else 0)
                    return -1 if ci == 0 else 1
                left = scores[i] * cj
                right = scores[j] * ci
                if left != right:
                    return -1 if left > right else 1
                return -1 if i < j else (1 if i > j else 0)

            candidates.sort(key=cmp_to_key(compare))
            ratio_orders.append(tuple(candidates))
        self.ratio_orders = tuple(ratio_orders)

    def _mask_cost(self, mask):
        totals = [0] * self.dimensions
        todo = mask
        while todo:
            bit = todo & -todo
            todo ^= bit
            cost = self.costs[bit.bit_length() - 1]
            for d in range(self.dimensions):
                totals[d] += cost[d]
        return tuple(totals)

    def _mask_score(self, mask):
        result = 0
        todo = mask
        while todo:
            bit = todo & -todo
            todo ^= bit
            result += self.scores[bit.bit_length() - 1]
        return result

    def _mask_value(self, mask):
        result = 0
        todo = mask
        while todo:
            bit = todo & -todo
            todo ^= bit
            result += self.values[bit.bit_length() - 1]
        return result

    def _mask_key(self, mask):
        return tuple(i for i in range(self.n) if mask & (1 << i))

    def _conflict_free(self, mask):
        todo = mask
        while todo:
            bit = todo & -todo
            todo ^= bit
            i = bit.bit_length() - 1
            if self.conflicts[i] & todo:
                return False
        return True

    def _close(self, mask):
        result = 0
        todo = mask
        while todo:
            bit = todo & -todo
            todo ^= bit
            result |= self.closure[bit.bit_length() - 1]
        return result

    def _upward(self, mask):
        result = 0
        todo = mask
        while todo:
            bit = todo & -todo
            todo ^= bit
            result |= self.reverse[bit.bit_length() - 1]
        return result

    def _selection_ban(self, mask):
        result = 0
        todo = mask
        while todo:
            bit = todo & -todo
            todo ^= bit
            result |= self.select_ban[bit.bit_length() - 1]
        return result

    def initial_state(self, must_select, must_exclude=0):
        selected = self._close(must_select)
        unavailable = self.base_unavailable | self._upward(must_exclude)
        if selected & unavailable or not self._conflict_free(selected):
            return None
        cost = self._mask_cost(selected)
        if any(cost[d] > self.budget[d] for d in range(self.dimensions)):
            return None
        unavailable |= self._selection_ban(selected)
        if selected & unavailable:
            return None
        return selected, unavailable, cost, self._mask_score(selected)

    def _upper_bound(self, candidate_mask, current_cost, current_score):
        """Integer upper bound from several fractional-knapsack relaxations."""
        candidate_mask &= self.positive_mask
        if not candidate_mask:
            return current_score, 0
        total = 0
        todo = candidate_mask
        while todo:
            bit = todo & -todo
            todo ^= bit
            total += self.scores[bit.bit_length() - 1]
        best_addition = total
        tight_dimension = 0
        for d in range(self.dimensions):
            capacity = self.budget[d] - current_cost[d]
            bound = 0
            for i in self.ratio_orders[d]:
                bit = 1 << i
                if not candidate_mask & bit:
                    continue
                cost = self.costs[i][d]
                score = self.scores[i]
                if cost == 0:
                    bound += score
                elif cost <= capacity:
                    capacity -= cost
                    bound += score
                else:
                    bound += score * capacity // cost
                    break
            if bound < best_addition:
                best_addition = bound
                tight_dimension = d
        return current_score + best_addition, tight_dimension

    def _independent(self, selected, candidates, include_zero=False):
        relevant = candidates & (
            self.nonnegative_mask if include_zero else self.positive_mask
        )
        todo = relevant
        while todo:
            bit = todo & -todo
            todo ^= bit
            i = bit.bit_length() - 1
            if self.closure[i] & ~selected != bit:
                return False
            if self.conflicts[i] & relevant:
                return False
        return True

    def maximize_score(self, must_select, must_exclude=0, target=None):
        """Return the maximum scalar objective, or None when infeasible.

        If *target* is supplied, search stops as soon as that (known global)
        target is attained.  This is used as a feasibility oracle while
        constructing the lexicographically first optimal ID tuple.
        """
        initial = self.initial_state(must_select, must_exclude)
        if initial is None:
            return None
        selected, unavailable, cost, score = initial
        best = score
        if target is not None and score == target:
            return target
        seen = set()
        found = False

        def search(selected_mask, unavailable_mask, totals, current_score):
            nonlocal best, found
            if found:
                return
            if current_score > best:
                best = current_score
                if target is not None and best == target:
                    found = True
                    return

            candidates = self.full & ~(selected_mask | unavailable_mask)
            positive = candidates & self.positive_mask
            if not positive:
                return
            upper, tight_dimension = self._upper_bound(
                positive, totals, current_score
            )
            cutoff = target if target is not None else best
            if upper <= cutoff:
                # With a target, equality can be useful unless it has already
                # been found.  In ordinary maximization ties are irrelevant.
                if target is None or upper < target:
                    return

            state_key = (selected_mask, unavailable_mask)
            if state_key in seen:
                return
            seen.add(state_key)

            # Once all positive choices are genuinely independent, solve the
            # residual multidimensional knapsack in one meet-in-the-middle
            # step instead of branching through its subsets.
            if positive.bit_count() >= 7 and self._independent(
                selected_mask, positive
            ):
                residual = self.mitm_best(
                    [i for i in range(self.n) if positive & (1 << i)],
                    selected_mask,
                    totals,
                    current_score,
                    tie_break=False,
                )
                if residual is not None and residual[0] > best:
                    best = residual[0]
                if target is not None and residual is not None and residual[0] == target:
                    found = True
                return

            # Prefer a variable with strong logical propagation.  Where all
            # impacts tie (ordinary knapsack), follow the tightest fractional
            # ratio order.
            ordered = self.ratio_orders[tight_dimension]
            chosen = None
            chosen_impact = -1
            for i in ordered:
                bit = 1 << i
                if not positive & bit:
                    continue
                impact = ((self.select_ban[i] | self.reverse[i]) & positive).bit_count()
                if impact > chosen_impact:
                    chosen = i
                    chosen_impact = impact
            add = self.closure[chosen] & ~selected_mask
            new_cost = list(totals)
            todo = add
            added_score = 0
            while todo:
                add_bit = todo & -todo
                todo ^= add_bit
                j = add_bit.bit_length() - 1
                added_score += self.scores[j]
                for d in range(self.dimensions):
                    new_cost[d] += self.costs[j][d]
            include_possible = not (add & unavailable_mask) and all(
                new_cost[d] <= self.budget[d] for d in range(self.dimensions)
            )
            if include_possible:
                new_unavailable = unavailable_mask | self._selection_ban(add)
                new_selected = selected_mask | add
                if not new_selected & new_unavailable:
                    search(
                        new_selected,
                        new_unavailable,
                        tuple(new_cost),
                        current_score + added_score,
                    )
            if not found:
                search(
                    selected_mask,
                    unavailable_mask | self.reverse[chosen],
                    totals,
                    current_score,
                )

        search(selected, unavailable, cost, score)
        if target is not None:
            return target if found or best == target else best
        return best

    def _enumerate_half(self, indices, capacity):
        zero = (0,) * self.dimensions
        records = [(zero, 0, 0)]
        for i in indices:
            additions = []
            item_cost = self.costs[i]
            item_score = self.scores[i]
            item_bit = 1 << i
            for cost, score, mask in records:
                shifted = tuple(
                    cost[d] + item_cost[d] for d in range(self.dimensions)
                )
                if all(shifted[d] <= capacity[d] for d in range(self.dimensions)):
                    additions.append((shifted, score + item_score, mask | item_bit))
            records.extend(additions)
        return records

    def mitm_best(
        self, optional, fixed_mask, fixed_cost, fixed_score, tie_break=True
    ):
        """Solve an independent optional-project knapsack exactly."""
        remaining = tuple(
            self.budget[d] - fixed_cost[d] for d in range(self.dimensions)
        )
        if any(x < 0 for x in remaining):
            return None
        optional = sorted(
            i
            for i in optional
            if self.scores[i] >= 0
            and all(self.costs[i][d] <= remaining[d] for d in range(self.dimensions))
        )
        split = len(optional) // 2
        left = self._enumerate_half(optional[:split], remaining)
        right_all = self._enumerate_half(optional[split:], remaining)

        key_cache = {}

        def right_key(mask):
            key = key_cache.get(mask)
            if key is None:
                key = self._mask_key(fixed_mask | mask)
                key_cache[mask] = key
            return key

        def better_right(a, b):
            """Return the better of right-half records a and b."""
            if a is None:
                return b
            if b is None:
                return a
            if a[1] != b[1]:
                return a if a[1] > b[1] else b
            if not tie_break:
                return a
            return a if right_key(a[2]) < right_key(b[2]) else b

        # Equal right costs are interchangeable for every capacity query.
        right_by_cost = {}
        for record in right_all:
            old = right_by_cost.get(record[0])
            right_by_cost[record[0]] = better_right(old, record)
        right = list(right_by_cost.values())

        best_score = None
        best_mask = 0

        def consider(left_record, right_record):
            nonlocal best_score, best_mask
            if right_record is None:
                return
            score = fixed_score + left_record[1] + right_record[1]
            mask = fixed_mask | left_record[2] | right_record[2]
            if best_score is None or score > best_score:
                best_score, best_mask = score, mask
            elif score == best_score and tie_break:
                if self._mask_key(mask) < self._mask_key(best_mask):
                    best_mask = mask

        if self.dimensions == 1:
            right.sort(key=lambda record: record[0][0])
            costs0 = []
            prefix = []
            current = None
            for record in right:
                current = better_right(current, record)
                costs0.append(record[0][0])
                prefix.append(current)
            for left_record in left:
                cap = remaining[0] - left_record[0][0]
                position = bisect_right(costs0, cap) - 1
                if position >= 0:
                    consider(left_record, prefix[position])

        elif self.dimensions == 2:
            right.sort(key=lambda record: record[0][0])
            y_values = sorted({record[0][1] for record in right})
            tree = [None] * (len(y_values) + 1)

            def update(record):
                position = bisect_left(y_values, record[0][1]) + 1
                while position < len(tree):
                    tree[position] = better_right(tree[position], record)
                    position += position & -position

            def query(limit):
                position = bisect_right(y_values, limit)
                result = None
                while position:
                    result = better_right(result, tree[position])
                    position -= position & -position
                return result

            queries = sorted(
                left,
                key=lambda record: remaining[0] - record[0][0],
            )
            cursor = 0
            for left_record in queries:
                cap0 = remaining[0] - left_record[0][0]
                while cursor < len(right) and right[cursor][0][0] <= cap0:
                    update(right[cursor])
                    cursor += 1
                consider(
                    left_record,
                    query(remaining[1] - left_record[0][1]),
                )

        else:
            right.sort(key=lambda record: record[0][0])
            y_values = sorted({record[0][1] for record in right})
            size = len(y_values)
            z_values = [[] for _ in range(size + 1)]
            for record in right:
                position = bisect_left(y_values, record[0][1]) + 1
                while position <= size:
                    z_values[position].append(record[0][2])
                    position += position & -position
            trees = [None] * (size + 1)
            for position in range(1, size + 1):
                values = sorted(set(z_values[position]))
                z_values[position] = values
                trees[position] = [None] * (len(values) + 1)

            def update(record):
                y_position = bisect_left(y_values, record[0][1]) + 1
                while y_position <= size:
                    values = z_values[y_position]
                    tree = trees[y_position]
                    z_position = bisect_left(values, record[0][2]) + 1
                    while z_position < len(tree):
                        tree[z_position] = better_right(tree[z_position], record)
                        z_position += z_position & -z_position
                    y_position += y_position & -y_position

            def query(y_limit, z_limit):
                y_position = bisect_right(y_values, y_limit)
                result = None
                while y_position:
                    values = z_values[y_position]
                    tree = trees[y_position]
                    z_position = bisect_right(values, z_limit)
                    while z_position:
                        result = better_right(result, tree[z_position])
                        z_position -= z_position & -z_position
                    y_position -= y_position & -y_position
                return result

            queries = sorted(
                left,
                key=lambda record: remaining[0] - record[0][0],
            )
            cursor = 0
            for left_record in queries:
                cap0 = remaining[0] - left_record[0][0]
                while cursor < len(right) and right[cursor][0][0] <= cap0:
                    update(right[cursor])
                    cursor += 1
                consider(
                    left_record,
                    query(
                        remaining[1] - left_record[0][1],
                        remaining[2] - left_record[0][2],
                    ),
                )

        return best_score, best_mask

    def exact_feasible_mask(self, mask, required_mask, target):
        if required_mask & ~mask:
            return False
        if self._close(mask) != mask or not self._conflict_free(mask):
            return False
        cost = self._mask_cost(mask)
        if any(cost[d] > self.budget[d] for d in range(self.dimensions)):
            return False
        return self._mask_score(mask) == target

    def lexicographic_optimum(self, required_mask, target):
        """Use the score optimizer as an oracle to construct the ID tie-break."""
        prefix = 0
        forbidden = 0
        last = -1
        while True:
            if self.exact_feasible_mask(prefix, required_mask, target):
                return prefix
            chosen = None
            for i in range(last + 1, self.n):
                bit = 1 << i
                trial = self.maximize_score(
                    required_mask | prefix | bit,
                    forbidden,
                    target=target,
                )
                if trial == target:
                    chosen = i
                    prefix |= bit
                    last = i
                    break
                forbidden |= bit
            if chosen is None:
                # The unconstrained optimum itself proves that this should be
                # unreachable; keep an explicit guard against implementation
                # mistakes rather than returning a non-optimal portfolio.
                raise RuntimeError("failed to reconstruct an optimal portfolio")


def solve(projects, budget, required=()):
    """Return the globally optimal feasible project subset.

    Invalid inputs raise ``ValueError``.  The function never mutates an input
    object or any list nested inside it.
    """
    records, budget_copy, required_copy = _validate(projects, budget, required)
    problem = _Problem(records, budget_copy)
    required_mask = 0
    for project_id in required_copy:
        required_mask |= 1 << problem.index[project_id]

    initial = problem.initial_state(required_mask)
    if initial is None:
        return None
    selected, unavailable, initial_cost, initial_score = initial
    candidates = problem.full & ~(selected | unavailable)

    # The most important 32-item scale case is a (possibly already-resolved)
    # independent multidimensional knapsack.  Its two halves contain at most
    # 2**16 subsets, and dominance queries combine them without a cross product.
    if problem._independent(selected, candidates, include_zero=True):
        optional = [
            i
            for i in range(problem.n)
            if candidates & (1 << i) and problem.scores[i] >= 0
        ]
        target, best_mask = problem.mitm_best(
            optional,
            selected,
            initial_cost,
            initial_score,
            tie_break=True,
        )
    else:
        target = problem.maximize_score(required_mask)
        best_mask = problem.lexicographic_optimum(required_mask, target)

    total_cost = problem._mask_cost(best_mask)
    return {
        "selected": [problem.ids[i] for i in range(problem.n) if best_mask & (1 << i)],
        "value": problem._mask_value(best_mask),
        "cost": list(total_cost),
    }
