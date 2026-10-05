"""Exact portfolio optimization by propagation and branch and bound."""

from functools import cmp_to_key


def solve(projects, budget, required=()):
    """Find the maximum value feasible closed subset, with exact tie breaks."""
    valid_number = lambda x: type(x) is int and x >= 0
    if type(budget) is not list or not 1 <= len(budget) <= 3 or not all(map(valid_number, budget)):
        raise ValueError("invalid budget")
    if type(projects) is not list or len(projects) > 32 or type(required) not in (list, tuple):
        raise ValueError("invalid input container")
    n, dimensions = len(projects), len(budget)
    names, values, costs, needs, bans = [], [], [], [], []
    seen = set()
    for p in projects:
        if type(p) is not dict or set(p) != {"id", "value", "cost", "requires", "excludes"}:
            raise ValueError("invalid project")
        name = p["id"]
        if type(name) is not str or not name or name in seen or type(p["value"]) is not int:
            raise ValueError("invalid ID or value")
        c = p["cost"]
        if type(c) is not list or len(c) != dimensions or not all(map(valid_number, c)):
            raise ValueError("invalid cost")
        for field in ("requires", "excludes"):
            refs = p[field]
            if type(refs) is not list or any(type(x) is not str or not x or x == name for x in refs):
                raise ValueError("invalid reference")
            if len(set(refs)) != len(refs):
                raise ValueError("duplicate reference")
        seen.add(name)
        names.append(name)
        values.append(p["value"])
        costs.append(tuple(c))
        needs.append(tuple(p["requires"]))
        bans.append(tuple(p["excludes"]))
    if any(type(x) is not str or x not in seen for x in required) or len(set(required)) != len(required):
        raise ValueError("invalid required IDs")
    if any(x not in seen for refs in needs + bans for x in refs):
        raise ValueError("unknown reference")
    lookup = {name: i for i, name in enumerate(names)}
    direct = [sum(1 << lookup[x] for x in refs) for refs in needs]
    conflict = [0] * n
    for i, refs in enumerate(bans):
        for x in refs:
            j = lookup[x]
            conflict[i] |= 1 << j
            conflict[j] |= 1 << i

    closure = [0] * n
    active = [False] * n

    def close(i):
        if active[i]:
            raise ValueError("dependency cycle")
        if closure[i]:
            return closure[i]
        active[i] = True
        result, todo = 1 << i, direct[i]
        while todo:
            bit = todo & -todo
            result |= close(bit.bit_length() - 1)
            todo ^= bit
        active[i] = False
        closure[i] = result
        return result

    for i in range(n):
        close(i)
    reverse = [sum(1 << j for j in range(n) if closure[j] & (1 << i)) for i in range(n)]
    closure_conflict, closure_forbid = [0] * n, [0] * n
    for i in range(n):
        todo = closure[i]
        while todo:
            bit = todo & -todo
            closure_conflict[i] |= conflict[bit.bit_length() - 1]
            todo ^= bit
        todo = closure_conflict[i]
        while todo:
            bit = todo & -todo
            closure_forbid[i] |= reverse[bit.bit_length() - 1]
            todo ^= bit

    def totals(mask):
        value, total = 0, [0] * dimensions
        while mask:
            bit = mask & -mask
            i = bit.bit_length() - 1
            value += values[i]
            for d in range(dimensions):
                total[d] += costs[i][d]
            mask ^= bit
        return value, tuple(total)

    initial = 0
    for x in required:
        initial |= closure[lookup[x]]
    todo, forbidden = initial, 0
    while todo:
        bit = todo & -todo
        i = bit.bit_length() - 1
        if conflict[i] & initial:
            return None
        forbidden |= closure_forbid[i]
        todo ^= bit
    initial_value, initial_cost = totals(initial)
    if any(initial_cost[d] > budget[d] for d in range(dimensions)):
        return None

    positive = [i for i in range(n) if values[i] > 0]

    def density_order(d):
        def compare(i, j):
            a, b = costs[i][d], costs[j][d]
            if not a or not b:
                return (a > 0) - (b > 0) if a != b else (values[j] > values[i]) - (values[j] < values[i])
            x, y = values[i] * b, values[j] * a
            return (y > x) - (y < x)
        return sorted(positive, key=cmp_to_key(compare))

    density = [density_order(d) for d in range(dimensions)]
    decision = sorted(range(n), key=lambda i: (-values[i], costs[i], names[i]))
    id_order = sorted(range(n), key=lambda i: names[i])

    def id_tuple(mask):
        return tuple(names[i] for i in id_order if mask & (1 << i))

    best_value, best_cost = initial_value, initial_cost
    best_ids = id_tuple(initial)
    universe = (1 << n) - 1

    def search(selected, excluded, value, cost):
        nonlocal best_value, best_cost, best_ids
        if value > best_value or (value == best_value and (cost, id_tuple(selected)) < (best_cost, best_ids)):
            best_value, best_cost, best_ids = value, cost, id_tuple(selected)
        available = universe & ~(selected | excluded)
        if not available:
            return
        bound = value + sum(values[i] for i in positive if available & (1 << i))
        if bound < best_value or (bound == best_value and cost > best_cost):
            return
        if bound == best_value and best_value == 0 and not best_ids:
            return
        for d in range(dimensions):
            room, upper = budget[d] - cost[d], value
            for i in density[d]:
                if not available & (1 << i):
                    continue
                weight = costs[i][d]
                if weight <= room:
                    room -= weight
                    upper += values[i]
                else:
                    upper += values[i] * room // weight
                    break
            if upper < best_value or (upper == best_value and cost > best_cost):
                return
            bound = min(bound, upper)
        if bound == best_value:
            gap = best_value - value
            lower_cost = list(cost)
            for d in range(dimensions):
                missing = gap
                for i in density[d]:
                    if not available & (1 << i):
                        continue
                    gain = values[i]
                    if gain >= missing:
                        if missing:
                            lower_cost[d] += (missing * costs[i][d] + gain - 1) // gain
                        break
                    missing -= gain
                    lower_cost[d] += costs[i][d]
            lower_cost = tuple(lower_cost)
            if lower_cost > best_cost:
                return
            if lower_cost == best_cost:
                remaining = gap
                count = 0
                if remaining:
                    for i in sorted(positive, key=lambda j: -values[j]):
                        if available & (1 << i):
                            count += 1
                            remaining -= values[i]
                            if remaining <= 0:
                                break
                chosen = selected
                for i in id_order:
                    if available & (1 << i) and count:
                        chosen |= 1 << i
                        count -= 1
                if chosen:
                    last = max(names[i] for i in id_order if chosen & (1 << i))
                    for i in id_order:
                        if available & (1 << i) and names[i] < last:
                            chosen |= 1 << i
                if id_tuple(chosen) >= best_ids:
                    return
        for i in decision:
            if available & (1 << i):
                break
        added = closure[i] & ~selected
        combined = selected | added
        if not (closure[i] & excluded) and not (closure_conflict[i] & combined):
            delta_value, delta_cost = totals(added)
            new_cost = tuple(cost[d] + delta_cost[d] for d in range(dimensions))
            if all(new_cost[d] <= budget[d] for d in range(dimensions)):
                search(combined, excluded | closure_forbid[i], value + delta_value, new_cost)
        search(selected, excluded | reverse[i], value, cost)

    search(initial, forbidden, initial_value, initial_cost)
    return {"selected": list(best_ids), "value": best_value, "cost": list(best_cost)}
