"""Exact constrained portfolio optimisation."""

from functools import cmp_to_key


def solve(projects, budget, required=()):
    """Return the best feasible portfolio described in TASK.md."""
    if type(projects) is not list or len(projects) > 32:
        raise ValueError("projects must be a list of at most 32 projects")
    if type(budget) is not list or not 1 <= len(budget) <= 3:
        raise ValueError("invalid budget")
    if any(type(x) is not int or x < 0 for x in budget):
        raise ValueError("invalid budget")
    if type(required) not in (list, tuple):
        raise ValueError("invalid required")
    keys = {"id", "value", "cost", "requires", "excludes"}
    ids, values, costs, raw_req, raw_exc = [], [], [], [], []
    seen = set()
    for p in projects:
        if type(p) is not dict or set(p) != keys:
            raise ValueError("invalid project")
        ident = p["id"]
        if type(ident) is not str or not ident or ident in seen:
            raise ValueError("invalid id")
        if type(p["value"]) is not int:
            raise ValueError("invalid value")
        if type(p["cost"]) is not list or len(p["cost"]) != len(budget):
            raise ValueError("invalid cost")
        if any(type(x) is not int or x < 0 for x in p["cost"]):
            raise ValueError("invalid cost")
        for field in ("requires", "excludes"):
            refs = p[field]
            if type(refs) is not list or any(type(x) is not str for x in refs):
                raise ValueError("invalid references")
            if len(set(refs)) != len(refs):
                raise ValueError("duplicate reference")
        seen.add(ident)
        ids.append(ident); values.append(p["value"]); costs.append(tuple(p["cost"]))
        raw_req.append(tuple(p["requires"])); raw_exc.append(tuple(p["excludes"]))
    pos = {name: i for i, name in enumerate(ids)}
    for i in range(len(ids)):
        for ref in raw_req[i] + raw_exc[i]:
            if ref not in pos or ref == ids[i]:
                raise ValueError("unknown or self reference")
    if any(type(x) is not str for x in required) or len(set(required)) != len(required):
        raise ValueError("invalid required")
    if any(x not in pos for x in required):
        raise ValueError("unknown required")

    n = len(ids)
    req = [tuple(pos[x] for x in refs) for refs in raw_req]
    colour, closure = [0] * n, [0] * n
    def close(i):
        if colour[i] == 1:
            raise ValueError("dependency cycle")
        if colour[i] == 2:
            return closure[i]
        colour[i] = 1
        mask = 1 << i
        for j in req[i]: mask |= close(j)
        colour[i] = 2; closure[i] = mask
        return mask
    for i in range(n): close(i)

    conflict = [0] * n
    for i, refs in enumerate(raw_exc):
        for name in refs:
            j = pos[name]; conflict[i] |= 1 << j; conflict[j] |= 1 << i
    bad = [False] * n
    for i, mask in enumerate(closure):
        m = mask
        while m:
            bit = m & -m; j = bit.bit_length() - 1
            if conflict[j] & mask: bad[i] = True; break
            m -= bit

    def totals(mask):
        value, cost = 0, [0] * len(budget)
        while mask:
            bit = mask & -mask; i = bit.bit_length() - 1
            value += values[i]
            for d in range(len(cost)): cost[d] += costs[i][d]
            mask -= bit
        return value, tuple(cost)
    selected = 0
    for name in required: selected |= closure[pos[name]]
    forbidden = 0; m = selected
    while m:
        bit = m & -m; i = bit.bit_length() - 1
        if conflict[i] & selected: return None
        forbidden |= conflict[i]; m -= bit
    start_value, start_cost = totals(selected)
    if any(start_cost[d] > budget[d] for d in range(len(budget))): return None

    def id_tuple(mask):
        return tuple(sorted(ids[i] for i in range(n) if mask >> i & 1))
    best_key = (-start_value, start_cost, id_tuple(selected)); best_mask = selected
    visited = set()

    def search(sel, forbid, value, cost):
        nonlocal best_key, best_mask
        state = (sel, forbid)
        if state in visited: return
        visited.add(state)
        here_ids = id_tuple(sel); key = (-value, cost, here_ids)
        if key < best_key: best_key, best_mask = key, sel
        candidates, positive = [], []
        for i in range(n):
            bit = 1 << i
            if sel & bit or forbid & bit or bad[i]: continue
            add = closure[i] & ~sel
            if add & forbid: continue
            av, ac = totals(add)
            nc = tuple(cost[d] + ac[d] for d in range(len(budget)))
            if any(nc[d] > budget[d] for d in range(len(budget))): continue
            candidates.append((i, add, av, ac))
            if values[i] > 0: positive.append(i)
        if not candidates: return

        # Fractional knapsack per dimension is a safe relaxation.
        upper = value + sum(values[i] for i in positive)
        for d in range(len(budget)):
            remain = budget[d] - cost[d]
            whole = sum(values[i] for i in positive if costs[i][d] == 0)
            items = [i for i in positive if costs[i][d] > 0]
            def density_cmp(a, b):
                left = values[a] * costs[b][d]
                right = values[b] * costs[a][d]
                return -1 if left > right else (1 if left < right else 0)
            items.sort(key=cmp_to_key(density_cmp))
            numerator, denominator = 0, 1
            for i in items:
                weight = costs[i][d]
                if weight <= remain: whole += values[i]; remain -= weight
                else: numerator, denominator = values[i] * remain, weight; break
            upper = min(upper, value + whole + numerator // denominator)
        best_value = -best_key[0]
        if upper < best_value: return
        if upper == best_value:
            # Lower-bound the cost of obtaining the still-needed positive value.
            # Each dimension is relaxed separately, as above, but in the
            # minimum-cost direction.
            need = best_value - value
            lower_cost = []
            for d in range(len(budget)):
                items = list(positive)
                def cheap_cmp(a, b):
                    left = costs[a][d] * values[b]
                    right = costs[b][d] * values[a]
                    return -1 if left < right else (1 if left > right else 0)
                items.sort(key=cmp_to_key(cheap_cmp))
                gained = spent = 0
                for i in items:
                    if gained + values[i] <= need:
                        gained += values[i]; spent += costs[i][d]
                    else:
                        rest = need - gained
                        spent += (costs[i][d] * rest + values[i] - 1) // values[i]
                        gained = need
                        break
                lower_cost.append(cost[d] + spent)
            lower_cost = tuple(lower_cost)
            if lower_cost > best_key[1]: return
            if lower_cost == best_key[1]:
                # Lexicographically smallest extension in a further relaxation:
                # closure members may be inserted for free and positive values
                # may be collected independently.
                possible = 0
                for i, add, av, ac in candidates: possible |= add
                chosen = set(here_ids)
                last = here_ids[-1] if here_ids else None
                gained = 0
                for j in sorted((j for j in range(n) if possible >> j & 1),
                                key=lambda j: ids[j]):
                    gain = values[j] if j in positive else 0
                    if (last is not None and ids[j] < last) or gained < need:
                        chosen.add(ids[j]); gained += max(0, gain)
                lower_ids = tuple(sorted(chosen))
                if lower_ids >= best_key[2]: return

        def score(item):
            i, add, av, ac = item
            return (add.bit_count() + conflict[i].bit_count(), values[i] > 0,
                    values[i], -sum(ac))
        i, add, av, ac = max(candidates, key=score)
        new_forbid = forbid; mm = add
        while mm:
            bit = mm & -mm
            new_forbid |= conflict[bit.bit_length() - 1]; mm -= bit
        new_cost = tuple(cost[d] + ac[d] for d in range(len(budget)))
        search(sel | add, new_forbid, value + av, new_cost)
        search(sel, forbid | (1 << i), value, cost)

    search(selected, forbidden, start_value, start_cost)
    final_value, final_cost = totals(best_mask)
    return {"selected": list(id_tuple(best_mask)), "value": final_value,
            "cost": list(final_cost)}
