"""Exact optimizer for small, constrained project portfolios."""

from functools import cmp_to_key

_KEYS = {"id", "value", "cost", "requires", "excludes"}


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def solve(projects, budget, required=()):
    """Return the globally best feasible portfolio, or ``None``."""
    if not isinstance(projects, list) or len(projects) > 32:
        raise ValueError("projects must be a list of at most 32 items")
    if (not isinstance(budget, list) or not 1 <= len(budget) <= 3 or
            any(not _integer(x) or x < 0 for x in budget)):
        raise ValueError("invalid budget")
    if not isinstance(required, (list, tuple)):
        raise ValueError("required must be a list or tuple")

    dims = len(budget)
    ids, values, costs, req_names, exc_names = [], [], [], [], []
    seen = set()
    for project in projects:
        if not isinstance(project, dict) or set(project) != _KEYS:
            raise ValueError("invalid project dictionary")
        name, value, cost = project["id"], project["value"], project["cost"]
        req, exc = project["requires"], project["excludes"]
        if not isinstance(name, str) or not name or name in seen:
            raise ValueError("invalid or duplicate project id")
        if not _integer(value):
            raise ValueError("invalid value")
        if (not isinstance(cost, list) or len(cost) != dims or
                any(not _integer(x) or x < 0 for x in cost)):
            raise ValueError("invalid cost")
        if not isinstance(req, list) or not isinstance(exc, list):
            raise ValueError("requires and excludes must be lists")
        if (any(not isinstance(x, str) or not x for x in req + exc) or
                len(set(req)) != len(req) or len(set(exc)) != len(exc)):
            raise ValueError("invalid or duplicate reference")
        seen.add(name)
        ids.append(name); values.append(value); costs.append(tuple(cost))
        req_names.append(tuple(req)); exc_names.append(tuple(exc))

    index = {name: i for i, name in enumerate(ids)}
    n = len(ids)
    for i in range(n):
        if ids[i] in req_names[i] or ids[i] in exc_names[i]:
            raise ValueError("self-reference")
        if any(x not in index for x in req_names[i] + exc_names[i]):
            raise ValueError("unknown reference")
    if (any(not isinstance(x, str) or not x for x in required) or
            len(set(required)) != len(required) or
            any(x not in index for x in required)):
        raise ValueError("invalid required id")

    direct, conflict = [0] * n, [0] * n
    for i in range(n):
        for name in req_names[i]:
            direct[i] |= 1 << index[name]
        for name in exc_names[i]:
            j = index[name]
            conflict[i] |= 1 << j
            conflict[j] |= 1 << i

    closure, state = [0] * n, [0] * n
    def visit(i):
        if state[i] == 1:
            raise ValueError("dependency cycle")
        if state[i] == 2:
            return closure[i]
        state[i] = 1
        mask, pending = 1 << i, direct[i]
        while pending:
            bit = pending & -pending; pending -= bit
            mask |= visit(bit.bit_length() - 1)
        closure[i], state[i] = mask, 2
        return mask
    for i in range(n):
        visit(i)

    reverse = [0] * n
    for i, mask in enumerate(closure):
        pending = mask
        while pending:
            bit = pending & -pending; pending -= bit
            reverse[bit.bit_length() - 1] |= 1 << i

    def expand_forbidden(mask):
        result = 0
        while mask:
            bit = mask & -mask; mask -= bit
            result |= reverse[bit.bit_length() - 1]
        return result

    def mask_stats(mask):
        val, total = 0, [0] * dims
        while mask:
            bit = mask & -mask; mask -= bit
            i = bit.bit_length() - 1; val += values[i]
            for d in range(dims): total[d] += costs[i][d]
        return val, tuple(total)

    def conflicts_with(mask):
        result = 0
        while mask:
            bit = mask & -mask; mask -= bit
            result |= conflict[bit.bit_length() - 1]
        return result

    required_mask = 0
    for name in required:
        required_mask |= closure[index[name]]
    base_conflicts = conflicts_with(required_mask)
    base_value, base_cost = mask_stats(required_mask)
    if (required_mask & base_conflicts or
            any(base_cost[d] > budget[d] for d in range(dims))):
        return None

    all_mask = (1 << n) - 1
    positive_mask = sum(1 << i for i in range(n) if values[i] > 0)
    forbidden = expand_forbidden(base_conflicts) & ~required_mask
    ratio_orders = []
    for d in range(dims):
        candidates = [i for i in range(n) if values[i] > 0 and costs[i][d] > 0]
        def compare(a, b, dim=d):
            left, right = values[a] * costs[b][dim], values[b] * costs[a][dim]
            return -1 if left > right else (1 if left < right else 0)
        ratio_orders.append(sorted(candidates, key=cmp_to_key(compare)))

    best_key, best_mask, memo = None, 0, set()
    def selected_tuple(mask):
        return tuple(sorted(ids[i] for i in range(n) if mask >> i & 1))
    def lexical_lower_bound(chosen, available):
        """Optimistic smallest ID tuple obtainable at unchanged total cost."""
        current = selected_tuple(chosen)
        if not current:
            return current
        last = current[-1]
        extras = (ids[i] for i in range(n) if available >> i & 1
                  and not any(costs[i]) and ids[i] < last)
        return tuple(sorted(current + tuple(extras)))
    def consider(mask, value, cost):
        nonlocal best_key, best_mask
        key = (-value, cost, selected_tuple(mask))
        if best_key is None or key < best_key:
            best_key, best_mask = key, mask

    def value_upper(available, value, cost):
        positive = available & positive_mask
        upper = value + sum(values[i] for i in range(n) if positive >> i & 1)
        for d in range(dims):
            remaining, bound = budget[d] - cost[d], value
            for i in range(n):
                if positive >> i & 1 and costs[i][d] == 0: bound += values[i]
            for i in ratio_orders[d]:
                if not (positive >> i & 1): continue
                c = costs[i][d]
                if c <= remaining:
                    bound += values[i]; remaining -= c
                else:
                    bound += values[i] * remaining // c
                    break
            upper = min(upper, bound)
        return upper

    def search(chosen, banned, value, cost):
        state_key = (chosen, banned)
        if state_key in memo: return
        memo.add(state_key)
        consider(chosen, value, cost)
        available = all_mask & ~chosen & ~banned
        if not available: return
        upper = value_upper(available, value, cost)
        if upper < -best_key[0] or (upper == -best_key[0] and cost > best_key[1]):
            return
        if (upper == -best_key[0] and cost == best_key[1] and
                lexical_lower_bound(chosen, available) >= best_key[2]):
            return
        choices = [i for i in range(n) if available >> i & 1]
        i = max(choices, key=lambda j: (
            max(values[j], 0) + sum(max(values[k], 0)
                for k in range(n) if closure[j] >> k & 1),
            (conflict[j] & available).bit_count(), reverse[j].bit_count()))
        added = closure[i] & ~chosen
        added_value, added_cost = mask_stats(added)
        next_cost = tuple(cost[d] + added_cost[d] for d in range(dims))
        combined = chosen | added
        selected_conflicts = conflicts_with(combined)
        can_include = (not (added & banned) and not (combined & selected_conflicts)
                       and all(next_cost[d] <= budget[d] for d in range(dims)))
        def include_branch():
            new_banned = banned | (expand_forbidden(selected_conflicts) & ~combined)
            search(combined, new_banned, value + added_value, next_cost)
        def exclude_branch():
            search(chosen, banned | reverse[i], value, cost)
        if can_include and added_value >= 0:
            include_branch(); exclude_branch()
        else:
            exclude_branch()
            if can_include: include_branch()

    search(required_mask, forbidden, base_value, base_cost)
    return {"selected": list(selected_tuple(best_mask)), "value": -best_key[0],
            "cost": list(best_key[1])}
