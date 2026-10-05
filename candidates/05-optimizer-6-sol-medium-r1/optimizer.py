"""Exact constrained portfolio optimization for at most 32 projects."""

from functools import cmp_to_key
from fractions import Fraction


def solve(projects, budget, required=()):
    """Return the feasible portfolio with the contract's three-level ordering."""
    if type(projects) is not list or len(projects) > 32:
        raise ValueError("invalid projects")
    if (type(budget) is not list or not 1 <= len(budget) <= 3 or
            any(type(x) is not int or x < 0 for x in budget)):
        raise ValueError("invalid budget")
    if type(required) not in (list, tuple):
        raise ValueError("invalid required")

    dims = len(budget)
    names, values, costs, dependencies, exclusions = [], [], [], [], []
    seen = set()
    keys = {"id", "value", "cost", "requires", "excludes"}
    for project in projects:
        if type(project) is not dict or project.keys() != keys:
            raise ValueError("invalid project dictionary")
        name, value = project["id"], project["value"]
        cost = project["cost"]
        requires, excludes = project["requires"], project["excludes"]
        if type(name) is not str or not name or name in seen:
            raise ValueError("invalid project ID")
        seen.add(name)
        if type(value) is not int:
            raise ValueError("invalid value")
        if (type(cost) is not list or len(cost) != dims or
                any(type(x) is not int or x < 0 for x in cost)):
            raise ValueError("invalid cost")
        for refs in (requires, excludes):
            if (type(refs) is not list or
                    any(type(x) is not str or not x for x in refs) or
                    len(set(refs)) != len(refs) or name in refs):
                raise ValueError("invalid references")
        names.append(name)
        values.append(value)
        costs.append(tuple(cost))
        dependencies.append(tuple(requires))
        exclusions.append(tuple(excludes))

    index = {name: i for i, name in enumerate(names)}
    if (any(type(name) is not str or name not in index for name in required) or
            len(set(required)) != len(required)):
        raise ValueError("invalid required IDs")
    for refs in dependencies + exclusions:
        if any(name not in index for name in refs):
            raise ValueError("unknown project reference")

    n = len(names)
    closure = [0] * n
    state = [0] * n

    def visit(i):
        if state[i] == 1:
            raise ValueError("dependency cycle")
        if state[i] == 2:
            return closure[i]
        state[i] = 1
        bits = 1 << i
        for name in dependencies[i]:
            bits |= visit(index[name])
        closure[i] = bits
        state[i] = 2
        return bits

    for i in range(n):
        visit(i)

    conflict = [0] * n
    for i, refs in enumerate(exclusions):
        for name in refs:
            j = index[name]
            conflict[i] |= 1 << j
            conflict[j] |= 1 << i

    dependents = [0] * n
    for i, bits in enumerate(closure):
        while bits:
            bit = bits & -bits
            dependents[bit.bit_length() - 1] |= 1 << i
            bits -= bit

    # Selecting i selects its closure. Block every project whose closure
    # conflicts with any member of that selection.
    blocked_by = [0] * n
    for i, bits in enumerate(closure):
        clashes = 0
        while bits:
            bit = bits & -bits
            clashes |= conflict[bit.bit_length() - 1]
            bits -= bit
        while clashes:
            bit = clashes & -clashes
            blocked_by[i] |= dependents[bit.bit_length() - 1]
            clashes -= bit

    selected = forbidden = 0
    for name in required:
        i = index[name]
        selected |= closure[i]
        forbidden |= blocked_by[i]
    if selected & forbidden:
        return None

    current_value = sum(values[i] for i in range(n) if selected >> i & 1)
    current_cost = tuple(sum(costs[i][d] for i in range(n) if selected >> i & 1)
                         for d in range(dims))
    if any(current_cost[d] > budget[d] for d in range(dims)):
        return None

    all_bits = (1 << n) - 1
    ids_order = sorted(range(n), key=names.__getitem__)
    positive = [i for i in range(n) if values[i] > 0]

    def density_cmp(d):
        def compare(i, j):
            ci, cj = costs[i][d], costs[j][d]
            if ci == 0:
                return -1 if cj else 0
            if cj == 0:
                return 1
            difference = values[i] * cj - values[j] * ci
            return -1 if difference > 0 else (1 if difference < 0 else 0)
        return compare

    density_orders = [sorted(positive, key=cmp_to_key(density_cmp(d)))
                      for d in range(dims)]
    # Branch ordering changes speed, never the result.
    branch_order = sorted(range(n), key=lambda i: (
        -Fraction(values[i], 1) /
        (1 + sum(Fraction(costs[i][d], budget[d] or 1)
                 for d in range(dims))), names[i]))

    best_value = best_cost = best_ids = None

    def ids_of(mask):
        return tuple(names[i] for i in ids_order if mask >> i & 1)

    def search(chosen, banned, value, total):
        nonlocal best_value, best_cost, best_ids
        if (best_value is None or value > best_value or
                (value == best_value and
                 (total < best_cost or
                  (total == best_cost and ids_of(chosen) < best_ids)))):
            best_value, best_cost, best_ids = value, total, ids_of(chosen)

        available = all_bits & ~(chosen | banned)
        if not available:
            return
        upper = value + sum(values[i] for i in positive if available >> i & 1)
        if upper < best_value:
            return

        # Fractional knapsack relaxes conflicts and dependency costs.
        for d, order in enumerate(density_orders):
            room = budget[d] - total[d]
            gain = 0
            for i in order:
                if not (available >> i & 1):
                    continue
                c = costs[i][d]
                if c <= room:
                    gain += values[i]
                    room -= c
                else:
                    gain += values[i] * room // c
                    break
            upper = min(upper, value + gain)
            if upper < best_value:
                return

        if upper == best_value:
            # Fractional minimum costs to gain enough value are lower bounds
            # for each component of the final cost tuple.
            need = best_value - value
            lower = []
            for d, order in enumerate(density_orders):
                remaining = need
                extra = 0
                for i in order:
                    if not (available >> i & 1):
                        continue
                    take = min(remaining, values[i])
                    if take <= 0:
                        break
                    if take == values[i]:
                        extra += costs[i][d]
                    else:
                        extra += (costs[i][d] * take + values[i] - 1) // values[i]
                    remaining -= take
                    if remaining == 0:
                        break
                lower.append(total[d] + extra)
            lower = tuple(lower)
            if lower > best_cost:
                return
            if lower == best_cost:
                # This ID tuple is no later lexicographically than any
                # completion that reaches the incumbent value.
                optimistic = []
                optimistic_value = 0
                remaining_selected = chosen
                for i in ids_order:
                    if optimistic_value >= best_value and not remaining_selected:
                        break
                    bit = 1 << i
                    if (chosen | available) & bit:
                        optimistic.append(names[i])
                        optimistic_value += values[i]
                    remaining_selected &= ~bit
                if tuple(optimistic) >= best_ids:
                    return

        i = next(i for i in branch_order if available >> i & 1)
        additions = closure[i] & ~chosen
        if not (closure[i] & banned) and not (blocked_by[i] & closure[i]):
            new_total = tuple(total[d] + sum(costs[j][d] for j in range(n)
                                             if additions >> j & 1)
                              for d in range(dims))
            if all(new_total[d] <= budget[d] for d in range(dims)):
                new_value = value + sum(values[j] for j in range(n)
                                        if additions >> j & 1)
                search(chosen | additions, banned | blocked_by[i],
                       new_value, new_total)
        search(chosen, banned | dependents[i], value, total)

    search(selected, forbidden, current_value, current_cost)
    return {"selected": list(best_ids), "value": best_value,
            "cost": list(best_cost)}
