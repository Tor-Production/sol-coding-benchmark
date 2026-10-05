"""Exact portfolio optimization using branch and bound."""

from functools import cmp_to_key


def solve(projects, budget, required=()):
    def valid_int(x):
        return type(x) is int and x >= 0

    if not isinstance(projects, list) or len(projects) > 32:
        raise ValueError("invalid projects")
    if not isinstance(budget, list) or not 1 <= len(budget) <= 3 or not all(map(valid_int, budget)):
        raise ValueError("invalid budget")
    if not isinstance(required, (list, tuple)):
        raise ValueError("invalid required")

    names = []
    for p in projects:
        if not isinstance(p, dict) or set(p) != {"id", "value", "cost", "requires", "excludes"}:
            raise ValueError("invalid project")
        if not isinstance(p["id"], str) or not p["id"] or type(p["value"]) is not int:
            raise ValueError("invalid ID or value")
        if not isinstance(p["cost"], list) or len(p["cost"]) != len(budget) or not all(map(valid_int, p["cost"])):
            raise ValueError("invalid cost")
        for key in ("requires", "excludes"):
            refs = p[key]
            if not isinstance(refs, list) or any(not isinstance(x, str) for x in refs):
                raise ValueError("invalid references")
            if len(set(refs)) != len(refs):
                raise ValueError("duplicate reference")
        names.append(p["id"])
    if len(set(names)) != len(names):
        raise ValueError("duplicate ID")

    ps = sorted(projects, key=lambda p: p["id"])
    ids = [p["id"] for p in ps]
    index = {name: i for i, name in enumerate(ids)}
    n = len(ps)
    full = (1 << n) - 1
    values = [p["value"] for p in ps]
    costs = [tuple(p["cost"]) for p in ps]
    deps = [0] * n
    conflicts = [0] * n
    for i, p in enumerate(ps):
        for name in p["requires"]:
            if name not in index or name == ids[i]:
                raise ValueError("invalid dependency")
            deps[i] |= 1 << index[name]
        for name in p["excludes"]:
            if name not in index or name == ids[i]:
                raise ValueError("invalid exclusion")
            j = index[name]
            conflicts[i] |= 1 << j
            conflicts[j] |= 1 << i
    required_mask = 0
    for name in required:
        if not isinstance(name, str) or name not in index or required_mask & (1 << index[name]):
            raise ValueError("invalid required ID")
        required_mask |= 1 << index[name]

    closure = [0] * n
    state = [0] * n

    def visit(i):
        if state[i] == 1:
            raise ValueError("dependency cycle")
        if state[i] == 2:
            return closure[i]
        state[i] = 1
        bits = 1 << i
        todo = deps[i]
        while todo:
            bit = todo & -todo
            todo -= bit
            bits |= visit(bit.bit_length() - 1)
        state[i] = 2
        closure[i] = bits
        return bits

    for i in range(n):
        visit(i)
    reverse = [0] * n
    impossible = 0
    for i, bits in enumerate(closure):
        todo = bits
        while todo:
            bit = todo & -todo
            todo -= bit
            j = bit.bit_length() - 1
            reverse[j] |= 1 << i
            if conflicts[j] & bits:
                impossible |= 1 << i

    def expand(bits, table):
        out = 0
        while bits:
            bit = bits & -bits
            bits -= bit
            out |= table[bit.bit_length() - 1]
        return out

    chosen = expand(required_mask, closure)
    banned = impossible | expand(expand(chosen, conflicts), reverse)
    initial_cost = tuple(sum(costs[i][d] for i in range(n) if chosen & (1 << i))
                         for d in range(len(budget)))
    if chosen & banned or any(initial_cost[d] > budget[d] for d in range(len(budget))):
        return None

    # Integer fractional-knapsack bounds are safe because objective values are integers.
    orders = []
    cost_orders = []
    for d in range(len(budget)):
        def compare(i, j):
            a, b = costs[i][d], costs[j][d]
            if not a:
                return -1 if b else 0
            if not b:
                return 1
            left, right = values[i] * b, values[j] * a
            return (right > left) - (right < left)
        orders.append(sorted(range(n), key=cmp_to_key(compare)))
        cost_orders.append([i for i in orders[-1] if values[i] > 0])
    best = None

    def record(mask, value, cost):
        nonlocal best
        names_tuple = tuple(ids[i] for i in range(n) if mask & (1 << i))
        if best is None or value > best[0] or (value == best[0] and (cost, names_tuple) < (best[1], best[2])):
            best = (value, cost, names_tuple)

    def search(mask, excluded, value, cost):
        available = full & ~(mask | excluded)
        upper = value + sum(values[i] for i in range(n) if available & (1 << i) and values[i] > 0)
        if best is not None and upper < best[0]:
            return
        for d, order in enumerate(orders):
            capacity = budget[d] - cost[d]
            bound = value
            for i in order:
                if not available & (1 << i) or values[i] <= 0:
                    continue
                c = costs[i][d]
                if c <= capacity:
                    bound += values[i]
                    capacity -= c
                else:
                    bound += values[i] * capacity // c
                    break
            upper = min(upper, bound)
        if best is not None:
            if upper < best[0] or (upper == best[0] and cost > best[1]):
                return
            if upper == best[0]:
                # Minimal possible sorted ID tuple among extensions of mask.
                low = mask | (available & ((1 << (mask.bit_length() - 1)) - 1)) if mask else 0
                if tuple(ids[i] for i in range(n) if low & (1 << i)) >= best[2]:
                    needed = max(0, best[0] - value)
                    minimum = []
                    for d, order in enumerate(cost_orders):
                        remaining = needed
                        lower = cost[d]
                        for i in order:
                            if not available & (1 << i):
                                continue
                            take = min(remaining, values[i])
                            lower += costs[i][d] * take // values[i]
                            remaining -= take
                            if not remaining:
                                break
                        minimum.append(lower)
                    if tuple(minimum) >= best[1]:
                        return
        record(mask, value, cost)
        if not available:
            return
        bit = available & -available
        i = bit.bit_length() - 1
        added = closure[i] & ~mask
        if not added & excluded:
            new_cost = tuple(cost[d] + sum(costs[j][d] for j in range(n) if added & (1 << j))
                             for d in range(len(budget)))
            if all(new_cost[d] <= budget[d] for d in range(len(budget))):
                new_mask = mask | added
                new_excluded = excluded | impossible | expand(expand(added, conflicts), reverse)
                if not new_mask & new_excluded:
                    search(new_mask, new_excluded,
                           value + sum(values[j] for j in range(n) if added & (1 << j)), new_cost)
        search(mask, excluded | reverse[i], value, cost)

    search(chosen, banned, sum(values[i] for i in range(n) if chosen & (1 << i)), initial_cost)
    return {"selected": list(best[2]), "value": best[0], "cost": list(best[1])}
