"""Exact optimizer for small, constrained project portfolios."""

from fractions import Fraction


_KEYS = {"id", "value", "cost", "requires", "excludes"}


def _integer(value):
    """True for contract integers (where bool deliberately is not an int)."""
    return type(value) is int


def solve(projects, budget, required=()):
    """Return the globally best feasible portfolio, or ``None``."""
    if not isinstance(projects, list) or len(projects) > 32:
        raise ValueError("projects must be a list of at most 32 items")
    if (not isinstance(budget, list) or not 1 <= len(budget) <= 3 or
            any(not _integer(x) or x < 0 for x in budget)):
        raise ValueError("budget must contain one to three nonnegative integers")
    if not isinstance(required, (list, tuple)):
        raise ValueError("required must be a list or tuple")

    dimensions = len(budget)
    ids, values, costs = [], [], []
    raw_requires, raw_excludes = [], []
    seen_ids = set()

    # Validate every record before doing any feasibility shortcuts.
    for project in projects:
        if not isinstance(project, dict) or set(project) != _KEYS:
            raise ValueError("each project must be a dictionary with exact keys")
        project_id = project["id"]
        value = project["value"]
        cost = project["cost"]
        requires = project["requires"]
        excludes = project["excludes"]
        if not isinstance(project_id, str) or not project_id or project_id in seen_ids:
            raise ValueError("project IDs must be unique nonempty strings")
        if not _integer(value):
            raise ValueError("project value must be an integer")
        if (not isinstance(cost, list) or len(cost) != dimensions or
                any(not _integer(x) or x < 0 for x in cost)):
            raise ValueError("invalid project cost")
        if not isinstance(requires, list) or not isinstance(excludes, list):
            raise ValueError("requires and excludes must be lists")
        for references in (requires, excludes):
            if (any(not isinstance(x, str) or not x for x in references) or
                    len(set(references)) != len(references)):
                raise ValueError("references must be distinct nonempty strings")
        seen_ids.add(project_id)
        ids.append(project_id)
        values.append(value)
        costs.append(tuple(cost))
        raw_requires.append(tuple(requires))
        raw_excludes.append(tuple(excludes))

    index = {project_id: i for i, project_id in enumerate(ids)}
    n = len(ids)
    requires_masks = [0] * n
    conflicts = [0] * n
    for i in range(n):
        for name in raw_requires[i]:
            if name not in index or name == ids[i]:
                raise ValueError("unknown or self dependency")
            requires_masks[i] |= 1 << index[name]
        for name in raw_excludes[i]:
            if name not in index or name == ids[i]:
                raise ValueError("unknown or self exclusion")
            j = index[name]
            conflicts[i] |= 1 << j
            conflicts[j] |= 1 << i

    if (any(not isinstance(x, str) or not x for x in required) or
            len(set(required)) != len(required) or
            any(x not in index for x in required)):
        raise ValueError("required contains invalid or duplicate IDs")

    # DFS simultaneously detects cycles and computes transitive closures.
    closures = [0] * n
    state = [0] * n

    def make_closure(i):
        if state[i] == 1:
            raise ValueError("dependency graph contains a cycle")
        if state[i] == 2:
            return closures[i]
        state[i] = 1
        result = 1 << i
        pending = requires_masks[i]
        while pending:
            bit = pending & -pending
            pending -= bit
            result |= make_closure(bit.bit_length() - 1)
        state[i] = 2
        closures[i] = result
        return result

    for i in range(n):
        make_closure(i)

    # Cache closure conflicts; an internally conflicting closure is impossible.
    closure_valid = [True] * n
    closure_conflicts = [0] * n
    for i, closure in enumerate(closures):
        pending = closure
        conflict_union = 0
        while pending:
            bit = pending & -pending
            pending -= bit
            conflict_union |= conflicts[bit.bit_length() - 1]
        closure_conflicts[i] = conflict_union
        closure_valid[i] = not bool(conflict_union & closure)

    required_mask = 0
    for name in required:
        required_mask |= closures[index[name]]

    def mask_totals(mask):
        total_value = 0
        total_cost = [0] * dimensions
        pending = mask
        while pending:
            bit = pending & -pending
            pending -= bit
            i = bit.bit_length() - 1
            total_value += values[i]
            for d in range(dimensions):
                total_cost[d] += costs[i][d]
        return total_value, total_cost

    current_value, current_cost = mask_totals(required_mask)
    initial_conflicts = 0
    pending = required_mask
    while pending:
        bit = pending & -pending
        pending -= bit
        initial_conflicts |= conflicts[bit.bit_length() - 1]
    if (initial_conflicts & required_mask or
            any(current_cost[d] > budget[d] for d in range(dimensions))):
        return None

    # The required closure supplies the initial incumbent (possibly empty).
    best_mask = required_mask
    best_value = current_value
    best_cost = tuple(current_cost)
    best_ids = tuple(sorted(ids[i] for i in range(n) if required_mask >> i & 1))

    # One fractional-knapsack relaxation per budget dimension.  The minimum of
    # these independent upper bounds is still a valid upper bound.
    density_orders = []
    for d in range(dimensions):
        density_orders.append(sorted(
            range(n),
            key=lambda i: (costs[i][d] != 0,
                           -Fraction(values[i], costs[i][d])
                           if costs[i][d] else 0),
        ))
    id_order = sorted(range(n), key=lambda i: ids[i])

    def result_key(mask):
        return tuple(ids[i] for i in id_order if mask >> i & 1)

    def optimistic_id_key(chosen, possible_mask):
        """A lower bound on the ID tuple of every completion.

        For a fixed nonempty tuple, inserting any available ID before its last
        member can only make the tuple lexicographically smaller; appending an
        ID after it can only make it larger.  Allowing all such insertions is a
        relaxation (they need not be jointly feasible), hence a safe bound.
        """
        chosen_names = result_key(chosen)
        if not chosen_names:
            return ()
        last = chosen_names[-1]
        return tuple(ids[i] for i in id_order
                     if (chosen >> i & 1) or
                     ((possible_mask >> i & 1) and ids[i] < last))

    def consider(mask, value, cost):
        nonlocal best_mask, best_value, best_cost, best_ids
        cost_tuple = tuple(cost)
        if value < best_value or (value == best_value and cost_tuple > best_cost):
            return
        names = result_key(mask)
        if (value > best_value or cost_tuple < best_cost or
                (cost_tuple == best_cost and names < best_ids)):
            best_mask, best_value, best_cost, best_ids = mask, value, cost_tuple, names

    def possible(i, chosen, forbidden, cost):
        if chosen >> i & 1 or closures[i] & forbidden or not closure_valid[i]:
            return False
        added = closures[i] & ~chosen
        for d in range(dimensions):
            extra = 0
            bits = added
            while bits:
                bit = bits & -bits
                bits -= bit
                extra += costs[bit.bit_length() - 1][d]
            if cost[d] + extra > budget[d]:
                return False
        return True

    def value_bound(value, cost, possible_mask):
        # Partition the positive candidates into conflict cliques.  A feasible
        # solution takes at most one member of each clique.
        clique_groups = []
        positive = [i for i in range(n)
                    if possible_mask >> i & 1 and values[i] > 0]
        positive.sort(key=lambda i: (conflicts[i] & possible_mask).bit_count(),
                      reverse=True)
        for i in positive:
            for group in clique_groups:
                if all(conflicts[i] >> j & 1 for j in group):
                    group.append(i)
                    break
            else:
                clique_groups.append([i])
        bound = value + sum(max(values[i] for i in group)
                            for group in clique_groups)

        for d in range(dimensions):
            remaining = budget[d] - cost[d]
            extra = 0
            for i in density_orders[d]:
                bit = 1 << i
                if not (possible_mask & bit) or values[i] <= 0:
                    continue
                item_cost = costs[i][d]
                if item_cost == 0:
                    extra += values[i]
                elif item_cost <= remaining:
                    remaining -= item_cost
                    extra += values[i]
                else:
                    # The real objective is integral, so flooring is safe.
                    extra += values[i] * remaining // item_cost
                    break
            candidate = value + extra
            bound = min(bound, candidate)
        return bound

    def search(chosen, forbidden, value, cost):
        possible_mask = 0
        for i in range(n):
            if possible(i, chosen, forbidden, cost):
                possible_mask |= 1 << i
        if not possible_mask:
            consider(chosen, value, cost)
            return

        upper = value_bound(value, cost, possible_mask)
        cost_tuple = tuple(cost)
        if upper < best_value or (upper == best_value and cost_tuple > best_cost):
            return
        if (upper == best_value and cost_tuple == best_cost and
                optimistic_id_key(chosen, possible_mask) >= best_ids):
            return

        # Consequential variables propagate early; density helps plain knapsacks.
        candidates = [i for i in range(n) if possible_mask >> i & 1]

        def score(i):
            affected = (closure_conflicts[i] | closures[i]).bit_count()
            normalized = sum((Fraction(costs[i][d], budget[d] + 1)
                              for d in range(dimensions)), Fraction())
            if normalized:
                density = (0, Fraction(values[i], 1) / normalized)
            else:
                density = (1, values[i])
            return affected, density, values[i], -i

        choice = max(candidates, key=score)
        bit = 1 << choice
        added = closures[choice] & ~chosen
        added_value = 0
        added_cost = [0] * dimensions
        bits = added
        new_conflicts = 0
        while bits:
            one = bits & -bits
            bits -= one
            j = one.bit_length() - 1
            added_value += values[j]
            new_conflicts |= conflicts[j]
            for d in range(dimensions):
                added_cost[d] += costs[j][d]

        def include_branch():
            search(chosen | added, forbidden | new_conflicts,
                   value + added_value,
                   [cost[d] + added_cost[d] for d in range(dimensions)])

        def exclude_branch():
            search(chosen, forbidden | bit, value, cost)

        if added_value >= 0:
            include_branch()
            exclude_branch()
        else:
            exclude_branch()
            include_branch()

    search(required_mask, initial_conflicts, current_value, current_cost)
    return {"selected": list(best_ids), "value": best_value, "cost": list(best_cost)}
