"""Exact project selection under budgets, prerequisites, and exclusions."""

from bisect import bisect_left, bisect_right
from fractions import Fraction
from functools import cmp_to_key


KEYS = {"id", "value", "cost", "requires", "excludes"}


def is_integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def bits(mask):
    while mask:
        bit = mask & -mask
        yield bit.bit_length() - 1
        mask -= bit


def validate(projects, budget, required):
    if not isinstance(projects, list) or len(projects) > 32:
        raise ValueError("projects must be a list of at most 32 projects")
    if (not isinstance(budget, list) or not 1 <= len(budget) <= 3
            or any(not is_integer(x) or x < 0 for x in budget)):
        raise ValueError("invalid budget")
    if not isinstance(required, (list, tuple)):
        raise ValueError("required must be a list or tuple")
    ids = []
    index = {}
    for project in projects:
        if not isinstance(project, dict) or set(project) != KEYS:
            raise ValueError("invalid project dictionary")
        name = project["id"]
        if not isinstance(name, str) or not name or name in index:
            raise ValueError("invalid or duplicate project ID")
        if not is_integer(project["value"]):
            raise ValueError("invalid project value")
        cost = project["cost"]
        if (not isinstance(cost, list) or len(cost) != len(budget)
                or any(not is_integer(x) or x < 0 for x in cost)):
            raise ValueError("invalid project cost")
        for key in ("requires", "excludes"):
            refs = project[key]
            if (not isinstance(refs, list)
                    or any(not isinstance(x, str) or not x or x == name for x in refs)
                    or len(refs) != len(set(refs))):
                raise ValueError("invalid project references")
        index[name] = len(ids)
        ids.append(name)
    if (any(not isinstance(x, str) or x not in index for x in required)
            or len(required) != len(set(required))):
        raise ValueError("invalid required IDs")
    if any(ref not in index for p in projects for key in ("requires", "excludes")
           for ref in p[key]):
        raise ValueError("unknown project reference")

    n = len(projects)
    deps = [tuple(index[x] for x in p["requires"]) for p in projects]
    closure = [0] * n
    visited = [0] * n

    def close(i):
        if visited[i] == 1:
            raise ValueError("dependency cycle")
        if visited[i] == 2:
            return closure[i]
        visited[i] = 1
        mask = 1 << i
        for j in deps[i]:
            mask |= close(j)
        closure[i] = mask
        visited[i] = 2
        return mask

    for i in range(n):
        close(i)
    conflicts = [0] * n
    for i, p in enumerate(projects):
        for name in p["excludes"]:
            j = index[name]
            conflicts[i] |= 1 << j
            conflicts[j] |= 1 << i
    forced = 0
    for name in required:
        forced |= closure[index[name]]
    return (ids, [p["value"] for p in projects],
            [tuple(p["cost"]) for p in projects], tuple(budget),
            closure, conflicts, forced)


def independent(ids, values, costs, budget, forced, allowed=None):
    """Exact meet-in-the-middle search with dominance queries."""
    n, d = len(ids), len(budget)
    if allowed is None:
        allowed = (1 << n) - 1
    base_mask = forced
    base_value = sum(values[i] for i in bits(forced))
    base_cost = tuple(sum(costs[i][j] for i in bits(forced)) for j in range(d))
    if any(base_cost[j] > budget[j] for j in range(d)):
        return None
    remaining = tuple(budget[j] - base_cost[j] for j in range(d))
    zero_zero = []
    optional = []
    for i in range(n):
        if not allowed & (1 << i):
            continue
        if forced & (1 << i):
            continue
        if values[i] == 0 and not any(costs[i]):
            zero_zero.append(i)
        elif values[i] > 0:
            if not any(costs[i]):
                base_mask |= 1 << i
                base_value += values[i]
            elif all(costs[i][j] <= remaining[j] for j in range(d)):
                optional.append(i)
    optional.sort(key=lambda i: ids[i])
    item_costs = [costs[i] for i in optional]
    item_values = [values[i] for i in optional]
    m = len(optional)

    def finish(mask, value, total_cost):
        chosen = [ids[i] for i in bits(base_mask)]
        chosen.extend(ids[optional[j]] for j in bits(mask))
        if chosen:
            last = max(chosen)
            chosen.extend(ids[i] for i in zero_zero if ids[i] < last)
        chosen.sort()
        return {"selected": chosen, "value": value, "cost": list(total_cost)}

    if not m:
        return finish(0, base_value, base_cost)
    if all(sum(item_costs[i][j] for i in range(m)) <= remaining[j]
           for j in range(d)):
        return finish((1 << m) - 1, base_value + sum(item_values),
                      tuple(base_cost[j] + sum(item_costs[i][j] for i in range(m))
                            for j in range(d)))

    def states(lo, hi):
        result = [((0,) * d, 0, 0)]
        for k in range(lo, hi):
            cost_k, value_k, bit_k = item_costs[k], item_values[k], 1 << k
            length = len(result)
            for t in range(length):
                old_cost, old_value, old_mask = result[t]
                new_cost = tuple(old_cost[j] + cost_k[j] for j in range(d))
                if all(new_cost[j] <= remaining[j] for j in range(d)):
                    result.append((new_cost, old_value + value_k,
                                   old_mask | bit_k))
        return result

    half = m // 2
    left = states(0, half)
    right_states = states(half, m)

    def lex_less(a, b):
        difference = a ^ b
        return bool(a & (difference & -difference))

    # Every optional item has positive value. Thus equal-value subsets cannot
    # be proper supersets, and their first differing ID determines the tie.
    by_cost = {}
    for cost, value, mask in right_states:
        old = by_cost.get(cost)
        if old is None or value > old[0] or (value == old[0]
                                               and lex_less(mask, old[1])):
            by_cost[cost] = (value, mask)
    right = [(cost, value, mask) for cost, (value, mask) in by_cost.items()]
    right.sort(key=lambda state: state[0][0])
    rcost = [state[0] for state in right]
    rvalue = [state[1] for state in right]
    rmask = [state[2] for state in right]

    def better(a, b):
        if b < 0:
            return True
        if rvalue[a] != rvalue[b]:
            return rvalue[a] > rvalue[b]
        if rcost[a] != rcost[b]:
            return rcost[a] < rcost[b]
        return lex_less(rmask[a], rmask[b])

    left.sort(key=lambda state: remaining[0] - state[0][0])
    best_value, best_cost, best_mask = None, None, 0

    def consider(left_state, right_index):
        nonlocal best_value, best_cost, best_mask
        if right_index < 0:
            return
        lcost, lvalue, lmask = left_state
        value = base_value + lvalue + rvalue[right_index]
        cost = tuple(base_cost[j] + lcost[j] + rcost[right_index][j]
                     for j in range(d))
        mask = lmask | rmask[right_index]
        if (best_value is None or value > best_value
                or (value == best_value and (cost < best_cost
                    or (cost == best_cost and lex_less(mask, best_mask))))):
            best_value, best_cost, best_mask = value, cost, mask

    point = 0
    if d == 1:
        prefix_best = -1
        for left_state in left:
            cap0 = remaining[0] - left_state[0][0]
            while point < len(right) and rcost[point][0] <= cap0:
                if better(point, prefix_best):
                    prefix_best = point
                point += 1
            consider(left_state, prefix_best)
    elif d == 2:
        xs = sorted({cost[1] for cost in rcost})
        tree = [-1] * (len(xs) + 1)

        def update(index):
            x = bisect_left(xs, rcost[index][1]) + 1
            while x < len(tree):
                if better(index, tree[x]):
                    tree[x] = index
                x += x & -x

        def query(cap):
            x = bisect_right(xs, cap)
            result = -1
            while x:
                if tree[x] >= 0 and better(tree[x], result):
                    result = tree[x]
                x -= x & -x
            return result

        for left_state in left:
            cap0 = remaining[0] - left_state[0][0]
            while point < len(right) and rcost[point][0] <= cap0:
                update(point)
                point += 1
            consider(left_state, query(remaining[1] - left_state[0][1]))
    else:
        xs = sorted({cost[1] for cost in rcost})
        ys = [[] for _ in range(len(xs) + 1)]
        for cost in rcost:
            x = bisect_left(xs, cost[1]) + 1
            while x < len(ys):
                ys[x].append(cost[2])
                x += x & -x
        for x in range(1, len(ys)):
            ys[x] = sorted(set(ys[x]))
        tree = [[-1] * (len(row) + 1) for row in ys]

        def update(index):
            x = bisect_left(xs, rcost[index][1]) + 1
            z = rcost[index][2]
            while x < len(ys):
                y = bisect_left(ys[x], z) + 1
                row = tree[x]
                while y < len(row):
                    if better(index, row[y]):
                        row[y] = index
                    y += y & -y
                x += x & -x

        def query(cap1, cap2):
            x = bisect_right(xs, cap1)
            result = -1
            while x:
                y = bisect_right(ys[x], cap2)
                row = tree[x]
                while y:
                    if row[y] >= 0 and better(row[y], result):
                        result = row[y]
                    y -= y & -y
                x -= x & -x
            return result

        for left_state in left:
            cap0 = remaining[0] - left_state[0][0]
            while point < len(right) and rcost[point][0] <= cap0:
                update(point)
                point += 1
            consider(left_state, query(remaining[1] - left_state[0][1],
                                       remaining[2] - left_state[0][2]))
    return finish(best_mask, best_value, best_cost)


def constrained(ids, values, costs, budget, closure, conflicts, forced):
    n, d = len(ids), len(budget)
    full_mask = (1 << n) - 1
    descendants = [0] * n
    for i, mask in enumerate(closure):
        for j in bits(mask):
            descendants[j] |= 1 << i
    ban_single = [0] * n
    for i in range(n):
        for j in bits(conflicts[i]):
            ban_single[i] |= descendants[j]
    ban_closure = [0] * n
    closure_cost, closure_value = [], []
    for i, mask in enumerate(closure):
        for j in bits(mask):
            ban_closure[i] |= ban_single[j]
        closure_cost.append(tuple(sum(costs[j][k] for j in bits(mask))
                                  for k in range(d)))
        closure_value.append(sum(values[j] for j in bits(mask)))

    selected = forced
    forbidden = 0
    for i in bits(selected):
        forbidden |= ban_single[i]
    if selected & forbidden:
        return None
    current_cost = tuple(sum(costs[i][j] for i in bits(selected))
                         for j in range(d))
    if any(current_cost[j] > budget[j] for j in range(d)):
        return None
    current_value = sum(values[i] for i in bits(selected))

    # Remove selections that cannot fit, conflict internally, or have become
    # impossible because a prerequisite is forbidden.
    while True:
        old = forbidden
        for i in range(n):
            bit = 1 << i
            if (selected | forbidden) & bit:
                continue
            addition = closure[i] & ~selected
            if (closure[i] & forbidden or closure[i] & ban_closure[i]
                    or any(sum(costs[j][k] for j in bits(addition))
                           + current_cost[k] > budget[k] for k in range(d))
                    or (descendants[i] == bit and
                        (values[i] < 0 or (values[i] == 0 and any(costs[i]))))):
                forbidden |= descendants[i]
        if forbidden == old:
            break

    lex_order = sorted(range(n), key=lambda i: ids[i])
    positive = [i for i in range(n) if values[i] > 0]
    by_value = sorted(positive, key=lambda i: -values[i])

    def ratio_order(dim):
        def compare(i, j):
            a, b = costs[i][dim], costs[j][dim]
            if a == 0 or b == 0:
                return (a > 0) - (b > 0)
            difference = values[j] * a - values[i] * b
            return (difference > 0) - (difference < 0)
        return sorted(positive, key=cmp_to_key(compare))

    density_orders = [ratio_order(j) for j in range(d)]
    cliques = []
    for order in (sorted(range(n), key=lambda i: -conflicts[i].bit_count()),
                  sorted(range(n), key=lambda i: -values[i]), lex_order):
        partition = []
        for i in order:
            bit = 1 << i
            for k, group in enumerate(partition):
                if group & ~conflicts[i] == 0:
                    partition[k] |= bit
                    break
            else:
                partition.append(bit)
        if partition not in cliques:
            cliques.append(partition)

    def branch_score(i):
        denominator = 1 + sum(Fraction(closure_cost[i][j], budget[j] + 1)
                              for j in range(d))
        return (Fraction(closure_value[i], 1) / denominator *
                Fraction(20 + conflicts[i].bit_count(), 20))

    branch_order = sorted(range(n), key=lambda i: (-branch_score(i), ids[i]))
    best_value = current_value
    best_cost = current_cost
    best_ids = tuple(ids[i] for i in lex_order if selected & (1 << i))

    def selected_ids(mask):
        return tuple(ids[i] for i in lex_order if mask & (1 << i))

    def optimistic_ids(mask, available, minimum_added):
        chosen = selected_ids(mask)
        last = chosen[-1] if chosen else None
        early, late = [], []
        for i in lex_order:
            if available & (1 << i):
                (early if last is not None and ids[i] < last else late).append(ids[i])
        early.extend(late[:max(0, minimum_added - len(early))])
        return tuple(sorted(chosen + tuple(early)))

    def search(mask, forbidden_mask, value, total_cost):
        nonlocal best_value, best_cost, best_ids
        if (value > best_value or (value == best_value and
                (total_cost < best_cost or
                 (total_cost == best_cost and selected_ids(mask) < best_ids)))):
            best_value, best_cost, best_ids = value, total_cost, selected_ids(mask)
        available = full_mask & ~(mask | forbidden_mask)
        if not available:
            return
        upper = value + sum(values[i] for i in positive if available & (1 << i))
        if upper < best_value:
            return
        for partition in cliques:
            bound = value
            for group in partition:
                choices = group & available
                if choices:
                    bound += max(0, *(values[i] for i in bits(choices)))
            upper = min(upper, bound)
            if upper < best_value:
                return
        for dim, order in enumerate(density_orders):
            capacity = budget[dim] - total_cost[dim]
            gain = 0
            for i in order:
                if not available & (1 << i):
                    continue
                cost = costs[i][dim]
                if cost <= capacity:
                    gain += values[i]
                    capacity -= cost
                else:
                    gain += values[i] * capacity // cost
                    break
            upper = min(upper, value + gain)
            if upper < best_value:
                return

        if upper == best_value:
            deficit = best_value - value
            lower_cost = []
            for dim, order in enumerate(density_orders):
                needed = deficit
                cost_total = total_cost[dim]
                for i in order:
                    if needed <= 0:
                        break
                    if available & (1 << i):
                        take = min(needed, values[i])
                        cost_total += costs[i][dim] * take // values[i]
                        needed -= take
                lower_cost.append(cost_total)
            lower_cost = tuple(lower_cost)
            if lower_cost > best_cost:
                return
            if lower_cost == best_cost:
                needed = deficit
                minimum_added = 0
                for i in by_value:
                    if needed <= 0:
                        break
                    if available & (1 << i):
                        needed -= values[i]
                        minimum_added += 1
                if needed <= 0 and optimistic_ids(mask, available,
                                                  minimum_added) >= best_ids:
                    return

        choice = next((i for i in branch_order
                       if available & (1 << i) and
                       ((closure[i] & available) != (1 << i) or
                        conflicts[i] & available)), None)
        if choice is None:
            completion = independent(ids, values, costs, budget, mask,
                                     mask | available)
            completion_value = completion["value"]
            completion_cost = tuple(completion["cost"])
            completion_ids = tuple(completion["selected"])
            if (completion_value > best_value or
                    (completion_value == best_value and
                     (completion_cost < best_cost or
                      (completion_cost == best_cost and
                       completion_ids < best_ids)))):
                best_value = completion_value
                best_cost = completion_cost
                best_ids = completion_ids
            return
        added = closure[choice] & ~mask
        next_mask = mask | added
        next_forbidden = forbidden_mask | ban_closure[choice]
        include_possible = not (added & forbidden_mask or
                                next_mask & next_forbidden)
        if include_possible:
            next_cost = tuple(total_cost[j] + sum(costs[i][j] for i in bits(added))
                              for j in range(d))
            include_possible = all(next_cost[j] <= budget[j] for j in range(d))
        if include_possible:
            next_value = value + sum(values[i] for i in bits(added))

        def include():
            if include_possible:
                search(next_mask, next_forbidden, next_value, next_cost)

        def exclude():
            search(mask, forbidden_mask | descendants[choice], value, total_cost)

        if include_possible and (next_value >= value or values[choice] > 0):
            include()
            exclude()
        else:
            exclude()
            include()

    search(selected, forbidden, current_value, current_cost)
    return {"selected": list(best_ids), "value": best_value,
            "cost": list(best_cost)}


def solve(projects, budget, required=()):
    """Return the globally best feasible portfolio, or None if impossible."""
    ids, values, costs, budget, closure, conflicts, forced = validate(
        projects, budget, required)
    if not any(conflicts) and all(mask == 1 << i for i, mask in enumerate(closure)):
        return independent(ids, values, costs, budget, forced)
    return constrained(ids, values, costs, budget, closure, conflicts, forced)
