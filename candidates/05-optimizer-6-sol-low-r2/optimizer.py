"""Exact portfolio optimizer."""

from functools import lru_cache
from fractions import Fraction


def solve(projects, budget, required=()):
    if not isinstance(projects, list) or len(projects) > 32:
        raise ValueError("projects")
    if not isinstance(budget, list) or not 1 <= len(budget) <= 3 or any(type(x) is not int or x < 0 for x in budget):
        raise ValueError("budget")
    if not isinstance(required, (list, tuple)):
        raise ValueError("required")
    m = len(budget)
    names = []
    for p in projects:
        if not isinstance(p, dict) or set(p) != {"id", "value", "cost", "requires", "excludes"}:
            raise ValueError("project")
        name = p["id"]
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("id")
        names.append(name)
        if type(p["value"]) is not int or not isinstance(p["cost"], list) or len(p["cost"]) != m or any(type(x) is not int or x < 0 for x in p["cost"]):
            raise ValueError("value or cost")
        for field in ("requires", "excludes"):
            refs = p[field]
            if not isinstance(refs, list) or any(not isinstance(x, str) or not x for x in refs) or len(set(refs)) != len(refs):
                raise ValueError("references")
    index = {name: i for i, name in enumerate(names)}
    if any(not isinstance(x, str) or x not in index for x in required) or len(set(required)) != len(required):
        raise ValueError("required ids")
    n = len(projects)
    deps = [0] * n
    conflicts = [0] * n
    for i, p in enumerate(projects):
        for name in p["requires"]:
            if name not in index or index[name] == i:
                raise ValueError("dependency")
            deps[i] |= 1 << index[name]
        for name in p["excludes"]:
            if name not in index or index[name] == i:
                raise ValueError("exclusion")
            j = index[name]
            conflicts[i] |= 1 << j
            conflicts[j] |= 1 << i
    closure = [0] * n
    visiting = 0
    def visit(i):
        nonlocal visiting
        bit = 1 << i
        if visiting & bit:
            raise ValueError("cycle")
        if closure[i]:
            return closure[i]
        visiting |= bit
        result = bit
        mask = deps[i]
        while mask:
            b = mask & -mask
            result |= visit(b.bit_length() - 1)
            mask -= b
        visiting &= ~bit
        closure[i] = result
        return result
    for i in range(n):
        visit(i)
    values = [p["value"] for p in projects]
    costs = [p["cost"] for p in projects]
    @lru_cache(None)
    def totals(mask):
        if not mask:
            return 0, (0,) * m
        bit = mask & -mask
        i = bit.bit_length() - 1
        v, c = totals(mask - bit)
        return v + values[i], tuple(c[d] + costs[i][d] for d in range(m))
    @lru_cache(None)
    def selected_ids(mask):
        return tuple(sorted(names[i] for i in range(n) if mask >> i & 1))
    bad = 0
    for i, c in enumerate(closure):
        _, total = totals(c)
        if any(total[d] > budget[d] for d in range(m)) or any(c & conflicts[j] for j in range(n) if c >> j & 1):
            bad |= 1 << i
    dependents = [0] * n
    for i, c in enumerate(closure):
        for j in range(n):
            if c >> j & 1:
                dependents[j] |= 1 << i
    selected = 0
    for name in required:
        selected |= closure[index[name]]
    rejected = bad
    for i in range(n):
        if selected >> i & 1:
            rejected |= conflicts[i]
    if selected & rejected or any(totals(selected)[1][d] > budget[d] for d in range(m)):
        return None
    all_mask = (1 << n) - 1
    best = None
    best_mask = 0
    seen = set()
    def search(chosen, banned):
        nonlocal best, best_mask
        state = chosen, banned
        if state in seen:
            return
        seen.add(state)
        value, cost = totals(chosen)
        if any(cost[d] > budget[d] for d in range(m)):
            return
        key = (-value, cost, selected_ids(chosen))
        if best is None or key < best:
            best, best_mask = key, chosen
        free = all_mask & ~(chosen | banned)
        if not free:
            return
        upper = value + sum(max(0, values[i]) for i in range(n) if free >> i & 1)
        if upper < -best[0]:
            return
        if upper == -best[0] and cost > best[1]:
            return
        for d in range(m):
            room = budget[d] - cost[d]
            bound = value
            items = []
            for i in range(n):
                if free >> i & 1 and values[i] > 0:
                    if costs[i][d] == 0:
                        bound += values[i]
                    else:
                        items.append(i)
            items.sort(key=lambda i: Fraction(values[i], costs[i][d]), reverse=True)
            for i in items:
                take = min(room, costs[i][d])
                bound += Fraction(values[i] * take, costs[i][d])
                room -= take
                if not room:
                    break
            if bound < -best[0]:
                return
            if bound == -best[0] and cost > best[1]:
                return
        i = max((j for j in range(n) if free >> j & 1),
                key=lambda j: (Fraction(max(values[j], 0), 1 + sum(costs[j])), (closure[j] | conflicts[j]).bit_count()))
        added = closure[i]
        if not added & banned:
            new_chosen = chosen | added
            new_banned = banned
            for j in range(n):
                if added >> j & 1:
                    new_banned |= conflicts[j]
            if not new_chosen & new_banned:
                search(new_chosen, new_banned)
        search(chosen, banned | dependents[i])
    search(selected, rejected)
    value, cost = totals(best_mask)
    return {"selected": list(selected_ids(best_mask)), "value": value, "cost": list(cost)}
