"""Exact optimization of small portfolios with prerequisites and conflicts."""

from fractions import Fraction


def _integer(value):
    return type(value) is int


def solve(projects, budget, required=()):
    """Return the best feasible portfolio, or None if mandatory work conflicts."""
    if type(projects) is not list or len(projects) > 32:
        raise ValueError("projects must be a list of at most 32 items")
    if (type(budget) is not list or not 1 <= len(budget) <= 3
            or any(not _integer(x) or x < 0 for x in budget)):
        raise ValueError("invalid budget")
    if type(required) not in (list, tuple):
        raise ValueError("required must be a list or tuple")

    dimensions = len(budget)
    ids = []
    values = []
    costs = []
    raw_requires = []
    raw_excludes = []
    seen = set()
    keys = {"id", "value", "cost", "requires", "excludes"}
    for project in projects:
        if type(project) is not dict or set(project) != keys:
            raise ValueError("invalid project fields")
        name = project["id"]
        value = project["value"]
        cost = project["cost"]
        requires = project["requires"]
        excludes = project["excludes"]
        if type(name) is not str or not name or name in seen:
            raise ValueError("invalid or duplicate ID")
        if not _integer(value):
            raise ValueError("invalid value")
        if (type(cost) is not list or len(cost) != dimensions
                or any(not _integer(x) or x < 0 for x in cost)):
            raise ValueError("invalid cost")
        for references in (requires, excludes):
            if (type(references) is not list
                    or any(type(x) is not str or not x for x in references)
                    or len(set(references)) != len(references)
                    or name in references):
                raise ValueError("invalid reference list")
        seen.add(name)
        ids.append(name)
        values.append(value)
        costs.append(tuple(cost))
        raw_requires.append(tuple(requires))
        raw_excludes.append(tuple(excludes))

    if (any(type(x) is not str or x not in seen for x in required)
            or len(set(required)) != len(required)):
        raise ValueError("invalid required IDs")
    index = {name: i for i, name in enumerate(ids)}
    n = len(ids)
    deps = [0] * n
    conflicts = [0] * n
    for i in range(n):
        for name in raw_requires[i]:
            if name not in index:
                raise ValueError("unknown prerequisite")
            deps[i] |= 1 << index[name]
        for name in raw_excludes[i]:
            if name not in index:
                raise ValueError("unknown exclusion")
            j = index[name]
            conflicts[i] |= 1 << j
            conflicts[j] |= 1 << i

    # The DFS validates cycles before any budget-based reduction.
    closure = [0] * n
    visiting = [False] * n
    def visit(i):
        if visiting[i]:
            raise ValueError("dependency cycle")
        if closure[i]:
            return closure[i]
        visiting[i] = True
        mask = 1 << i
        rest = deps[i]
        while rest:
            bit = rest & -rest
            mask |= visit(bit.bit_length() - 1)
            rest -= bit
        visiting[i] = False
        closure[i] = mask
        return mask

    for i in range(n):
        visit(i)

    all_mask = (1 << n) - 1
    # If a prerequisite is forbidden, all projects depending on it are too.
    dependents = [0] * n
    for i, mask in enumerate(closure):
        rest = mask
        while rest:
            bit = rest & -rest
            dependents[bit.bit_length() - 1] |= 1 << i
            rest -= bit

    def members(mask):
        while mask:
            bit = mask & -mask
            yield bit.bit_length() - 1
            mask -= bit

    def total(mask):
        v = 0
        c = [0] * dimensions
        for i in members(mask):
            v += values[i]
            for d in range(dimensions):
                c[d] += costs[i][d]
        return v, tuple(c)

    def conflicting(mask):
        return any(conflicts[i] & mask for i in members(mask))

    mandatory = 0
    for name in required:
        mandatory |= closure[index[name]]
    base_value, base_cost = total(mandatory)
    if conflicting(mandatory) or any(base_cost[d] > budget[d] for d in range(dimensions)):
        return None

    forbidden = 0
    for i in members(mandatory):
        forbidden |= conflicts[i]
    # A project with an impossible closure can be permanently discarded.
    for i in range(n):
        _, c = total(closure[i])
        if conflicting(closure[i]) or any(c[d] > budget[d] for d in range(dimensions)):
            forbidden |= 1 << i
    forbidden = sum((1 << i for i in range(n) if closure[i] & forbidden), 0)
    if mandatory & forbidden:
        return None

    sorted_indices = sorted(range(n), key=lambda i: ids[i])
    def names(mask):
        return tuple(ids[i] for i in sorted_indices if mask & (1 << i))

    best_value = base_value
    best_cost = base_cost
    best_names = names(mandatory)

    # Search promising closures first. This ordering affects speed only.
    def priority(i):
        v, c = total(closure[i])
        return Fraction(v, 1 + sum(c))
    order = sorted(range(n), key=lambda i: (-priority(i), ids[i]))
    positive_mask = sum(1 << i for i in range(n) if values[i] > 0)
    free_positive = [sum(1 << i for i in range(n)
                         if values[i] > 0 and costs[i][d] == 0)
                     for d in range(dimensions)]
    ratio_order = [sorted((i for i in range(n)
                           if values[i] > 0 and costs[i][d] > 0),
                          key=lambda i: Fraction(values[i], costs[i][d]),
                          reverse=True)
                   for d in range(dimensions)]

    def optimistic(value, cost, available):
        """Fractional knapsack relaxation in each budget dimension."""
        positives = list(members(available & positive_mask))
        if not positives:
            return value
        result = value + sum(values[i] for i in positives)
        for d in range(dimensions):
            capacity = budget[d] - cost[d]
            free = sum(values[i] for i in members(available & free_positive[d]))
            bound = value + free
            for i in ratio_order[d]:
                if not available & (1 << i):
                    continue
                take = min(capacity, costs[i][d])
                bound += values[i] * take // costs[i][d]
                capacity -= take
                if capacity == 0:
                    break
            if bound < result:
                result = bound
        return result

    def lex_lower(selected, available, need):
        """Smallest possible ID tuple ignoring costs and constraints."""
        positives = sorted((values[i] for i in members(available & positive_mask)), reverse=True)
        count = 0
        while need > 0 and count < len(positives):
            need -= positives[count]
            count += 1
        if need > 0:
            return None
        chosen = selected
        largest = max((ids[i] for i in members(selected)), default=None)
        for i in sorted_indices:
            bit = 1 << i
            if available & bit and (count > 0 or (largest is not None and ids[i] < largest)):
                chosen |= bit
                if count:
                    count -= 1
        return names(chosen)

    def cost_lower(cost, available, need):
        """Coordinatewise fractional cost lower bound for a needed value."""
        if need <= 0:
            return cost
        lower = []
        for d in range(dimensions):
            remaining = need - sum(values[i] for i in members(available & free_positive[d]))
            if remaining <= 0:
                lower.append(cost[d])
                continue
            extra = 0
            for i in ratio_order[d]:
                if not available & (1 << i):
                    continue
                take = min(remaining, values[i])
                extra += costs[i][d] * take // values[i]
                remaining -= take
                if not remaining:
                    break
            # Flooring partial items weakens the bound but remains safe.
            lower.append(cost[d] + extra)
        return tuple(lower)

    def search(selected, banned, value, cost):
        nonlocal best_value, best_cost, best_names
        available = all_mask & ~(selected | banned)
        candidate_names = names(selected)
        if (value > best_value or (value == best_value and
                (cost < best_cost or (cost == best_cost and candidate_names < best_names)))):
            best_value, best_cost, best_names = value, cost, candidate_names
        if not available:
            return
        upper = optimistic(value, cost, available)
        if upper < best_value:
            return
        if upper == best_value:
            lower_cost = cost_lower(cost, available, best_value - value)
            if lower_cost > best_cost:
                return
            if lower_cost == best_cost:
                lower = lex_lower(selected, available, best_value - value)
                if lower is None or lower >= best_names:
                    return

        i = next(i for i in order if available & (1 << i))
        added = closure[i] & ~selected
        if not (added & banned):
            new_selected = selected | added
            if not conflicting(new_selected):
                extra_value, extra_cost = total(added)
                new_cost = tuple(cost[d] + extra_cost[d] for d in range(dimensions))
                if all(new_cost[d] <= budget[d] for d in range(dimensions)):
                    blocked = banned
                    for j in members(added):
                        blocked |= conflicts[j]
                    blocked = sum((1 << j for j in range(n) if closure[j] & blocked), 0)
                    if not (blocked & new_selected):
                        search(new_selected, blocked, value + extra_value, new_cost)
        search(selected, banned | dependents[i], value, cost)

    search(mandatory, forbidden, base_value, base_cost)
    return {"selected": list(best_names), "value": best_value, "cost": list(best_cost)}
