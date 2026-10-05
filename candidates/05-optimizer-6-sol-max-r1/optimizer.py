"""Exact optimizer for small, constrained project portfolios."""

from bisect import bisect_left, bisect_right
from fractions import Fraction
from functools import cmp_to_key


_KEYS = {"id", "value", "cost", "requires", "excludes"}


def _lex_less(a, b):
    """Compare sets represented by bits in increasing project-ID order."""
    if a == b:
        return False
    first = (a ^ b) & -(a ^ b)
    later = ~((first << 1) - 1)
    if a & first:
        return bool(b & later)
    return not bool(a & later)


def _lex_less_with_tail(a, b):
    """Compare left halves followed by the same nonempty right half."""
    if a == b:
        return False
    return bool(a & ((a ^ b) & -(a ^ b)))


def _validated(projects, budget, required):
    if not isinstance(projects, list) or len(projects) > 32:
        raise ValueError("projects must be a list of at most 32 items")
    if (not isinstance(budget, list) or not 1 <= len(budget) <= 3
            or any(isinstance(x, bool) or not isinstance(x, int) or x < 0
                   for x in budget)):
        raise ValueError("invalid budget")
    if not isinstance(required, (list, tuple)):
        raise ValueError("required must be a list or tuple")

    by_id = {}
    for project in projects:
        if not isinstance(project, dict) or set(project) != _KEYS:
            raise ValueError("invalid project dictionary")
        name = project["id"]
        if not isinstance(name, str) or not name or name in by_id:
            raise ValueError("invalid or duplicate project ID")
        value = project["value"]
        cost = project["cost"]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("invalid project value")
        if (not isinstance(cost, list) or len(cost) != len(budget)
                or any(isinstance(x, bool) or not isinstance(x, int) or x < 0
                       for x in cost)):
            raise ValueError("invalid project cost")
        for field in ("requires", "excludes"):
            refs = project[field]
            if not isinstance(refs, list):
                raise ValueError("references must be lists")
            seen = set()
            for ref in refs:
                if not isinstance(ref, str) or not ref or ref == name or ref in seen:
                    raise ValueError("invalid or duplicate reference")
                seen.add(ref)
        by_id[name] = project

    names = sorted(by_id)
    index = {name: i for i, name in enumerate(names)}
    n = len(names)
    values = [0] * n
    costs = [None] * n
    requires = [0] * n
    excludes = [0] * n
    for i, name in enumerate(names):
        project = by_id[name]
        values[i] = project["value"]
        costs[i] = tuple(project["cost"])
        for ref in project["requires"]:
            if ref not in index:
                raise ValueError("unknown dependency")
            requires[i] |= 1 << index[ref]
        for ref in project["excludes"]:
            if ref not in index:
                raise ValueError("unknown exclusion")
            j = index[ref]
            excludes[i] |= 1 << j
            excludes[j] |= 1 << i

    required_mask = 0
    for name in required:
        if not isinstance(name, str) or name not in index:
            raise ValueError("invalid required ID")
        bit = 1 << index[name]
        if required_mask & bit:
            raise ValueError("duplicate required ID")
        required_mask |= bit

    state = [0] * n
    closure = [0] * n

    def visit(i):
        if state[i] == 1:
            raise ValueError("dependency cycle")
        if state[i] == 2:
            return closure[i]
        state[i] = 1
        result = 1 << i
        todo = requires[i]
        while todo:
            bit = todo & -todo
            todo -= bit
            result |= visit(bit.bit_length() - 1)
        closure[i] = result
        state[i] = 2
        return result

    for i in range(n):
        visit(i)
    return names, values, costs, requires, excludes, closure, required_mask


def _half_points(values, costs, budget, start, end, forced, left):
    """Keep the best half portfolio for each cost vector."""
    count = end - start
    limit = 1 << count
    dim = len(budget)
    sums = [[0] * limit for _ in range(dim)]
    totals = [0] * limit
    points = {}
    for mask in range(limit):
        if mask:
            bit = mask & -mask
            j = bit.bit_length() - 1
            prev = mask - bit
            totals[mask] = totals[prev] + values[start + j]
            for d in range(dim):
                sums[d][mask] = sums[d][prev] + costs[start + j][d]
        if mask & forced != forced:
            continue
        cost = tuple(sums[d][mask] for d in range(dim))
        if any(cost[d] > budget[d] for d in range(dim)):
            continue
        value = totals[mask]
        old = points.get(cost)
        if old is None or value > old[0]:
            points[cost] = (value, mask, mask)
        elif value == old[0]:
            normal = mask if _lex_less(mask, old[1]) else old[1]
            tail = (mask if left and _lex_less_with_tail(mask, old[2])
                    else old[2])
            points[cost] = (value, normal, tail)
    return [(cost, *data) for cost, data in points.items()]


def _solve_independent(names, values, costs, budget, required_mask):
    """Meet in the middle, with offline multidimensional range maxima."""
    n = len(names)
    split = n // 2
    left_forced = required_mask & ((1 << split) - 1)
    right_forced = required_mask >> split
    left = _half_points(values, costs, budget, 0, split, left_forced, True)
    right = _half_points(values, costs, budget, split, n, right_forced, False)
    if not left or not right:
        return None

    dim = len(budget)
    right.sort(key=lambda p: p[0][0])
    queries = sorted(left, key=lambda p: budget[0] - p[0][0])

    def better(p, q):
        if q is None:
            return True
        if p[1] != q[1]:
            return p[1] > q[1]
        if p[0] != q[0]:
            return p[0] < q[0]
        return _lex_less(p[2], q[2])

    if dim >= 2:
        ys = sorted({p[0][1] for p in right})
        y_index = {y: i + 1 for i, y in enumerate(ys)}
        if dim == 2:
            tree = [None] * (len(ys) + 1)
        else:
            zs = [[] for _ in range(len(ys) + 1)]
            for p in right:
                i = y_index[p[0][1]]
                while i <= len(ys):
                    zs[i].append(p[0][2])
                    i += i & -i
            zs = [sorted(set(z)) for z in zs]
            tree = [[None] * (len(z) + 1) for z in zs]

    def insert(p):
        nonlocal top
        if dim == 1:
            if better(p, top):
                top = p
        elif dim == 2:
            i = y_index[p[0][1]]
            while i <= len(ys):
                if better(p, tree[i]):
                    tree[i] = p
                i += i & -i
        else:
            i = y_index[p[0][1]]
            while i <= len(ys):
                j = bisect_left(zs[i], p[0][2]) + 1
                row = tree[i]
                while j < len(row):
                    if better(p, row[j]):
                        row[j] = p
                    j += j & -j
                i += i & -i

    def query(remaining):
        if dim == 1:
            return top
        answer = None
        i = bisect_right(ys, remaining[1])
        if dim == 2:
            while i:
                p = tree[i]
                if p is not None and better(p, answer):
                    answer = p
                i -= i & -i
        else:
            while i:
                j = bisect_right(zs[i], remaining[2])
                row = tree[i]
                while j:
                    p = row[j]
                    if p is not None and better(p, answer):
                        answer = p
                    j -= j & -j
                i -= i & -i
        return answer

    top = None
    cursor = 0
    best_value = None
    best_cost = None
    best_mask = 0
    last_remaining = None
    last_answer = None
    for left_cost, left_value, normal, tail in queries:
        remaining = tuple(budget[d] - left_cost[d] for d in range(dim))
        while cursor < len(right) and right[cursor][0][0] <= remaining[0]:
            insert(right[cursor])
            cursor += 1
        if remaining == last_remaining:
            p = last_answer
        else:
            p = query(remaining)
            last_remaining, last_answer = remaining, p
        if p is None:
            continue
        value = left_value + p[1]
        cost = tuple(left_cost[d] + p[0][d] for d in range(dim))
        mask = (tail if p[2] else normal) | (p[2] << split)
        if (best_value is None or value > best_value
                or (value == best_value and
                    (cost < best_cost or
                     (cost == best_cost and _lex_less(mask, best_mask))))):
            best_value, best_cost, best_mask = value, cost, mask

    if best_value is None:
        return None
    return {"selected": [names[i] for i in range(n) if best_mask & (1 << i)],
            "value": best_value, "cost": list(best_cost)}


def _solve_constrained(names, values, costs, requires, excludes, closure,
                       budget, required_mask):
    n = len(names)
    dim = len(budget)
    full = (1 << n) - 1
    descendants = [0] * n
    for i, group in enumerate(closure):
        todo = group
        while todo:
            bit = todo & -todo
            todo -= bit
            descendants[bit.bit_length() - 1] |= 1 << i

    def totals(mask):
        value = 0
        cost = [0] * dim
        while mask:
            bit = mask & -mask
            mask -= bit
            i = bit.bit_length() - 1
            value += values[i]
            for d in range(dim):
                cost[d] += costs[i][d]
        return value, tuple(cost)

    def conflict_bits(mask):
        result = 0
        while mask:
            bit = mask & -mask
            mask -= bit
            result |= excludes[bit.bit_length() - 1]
        return result

    def ban_descendants(mask):
        result = 0
        while mask:
            bit = mask & -mask
            mask -= bit
            result |= descendants[bit.bit_length() - 1]
        return result

    invalid = 0
    for i, group in enumerate(closure):
        _, cost = totals(group)
        if (conflict_bits(group) & group
                or any(cost[d] > budget[d] for d in range(dim))):
            invalid |= 1 << i
    invalid = ban_descendants(invalid)

    selected = 0
    todo = required_mask
    while todo:
        bit = todo & -todo
        todo -= bit
        selected |= closure[bit.bit_length() - 1]
    value, cost = totals(selected)
    if (selected & invalid or conflict_bits(selected) & selected
            or any(cost[d] > budget[d] for d in range(dim))):
        return None
    banned = invalid | ban_descendants(conflict_bits(selected))
    if selected & banned:
        return None

    active = full & ~(selected | banned)
    if active:
        independent = True
        todo = active
        while todo:
            bit = todo & -todo
            todo -= bit
            i = bit.bit_length() - 1
            if closure[i] & active != bit or excludes[i] & active:
                independent = False
                break
        if independent:
            keep = selected | active
            local_names = []
            local_values = []
            local_costs = []
            local_required = 0
            todo = keep
            while todo:
                bit = todo & -todo
                todo -= bit
                i = bit.bit_length() - 1
                j = len(local_names)
                local_names.append(names[i])
                local_values.append(values[i])
                local_costs.append(costs[i])
                if selected & bit:
                    local_required |= 1 << j
            return _solve_independent(local_names, local_values, local_costs,
                                      budget, local_required)

    # Small disconnected constraint components can be optimized exactly when
    # forming a bound. Budget limits are omitted, so the result remains an
    # upper bound on every completion of a search state.
    neighbors = [requires[i] | excludes[i] for i in range(n)]
    for i in range(n):
        todo = requires[i]
        while todo:
            bit = todo & -todo
            todo -= bit
            neighbors[bit.bit_length() - 1] |= 1 << i
    components = []
    unseen = full
    while unseen:
        frontier = unseen & -unseen
        group = 0
        while frontier:
            bit = frontier & -frontier
            frontier -= bit
            if group & bit:
                continue
            group |= bit
            frontier |= neighbors[bit.bit_length() - 1] & ~group
        unseen &= ~group
        if 1 < group.bit_count() <= 8:
            members = [i for i in range(n) if group & (1 << i)]
            options = []
            for local in range(1 << len(members)):
                sub = sum(1 << members[j] for j in range(len(members))
                          if local & (1 << j))
                valid = True
                option_value = 0
                for i in members:
                    if sub & (1 << i):
                        if requires[i] & ~sub or excludes[i] & sub:
                            valid = False
                            break
                        option_value += values[i]
                if valid:
                    options.append((sub, option_value))
            components.append((group, options, {}))

    # Fractional knapsack relaxations provide an integer upper bound for each
    # budget dimension. Conflicts and dependencies only reduce feasibility.
    positive = [i for i in range(n) if values[i] > 0]
    positive_mask = sum(1 << i for i in positive)
    ratio_orders = []
    cost_orders = []
    for d in range(dim):
        def compare(i, j):
            ci, cj = costs[i][d], costs[j][d]
            if not ci:
                return -1 if cj else 0
            if not cj:
                return 1
            x = values[i] * cj - values[j] * ci
            return -1 if x > 0 else (1 if x < 0 else 0)
        ratio_orders.append(sorted(positive, key=cmp_to_key(compare)))

        def compare_cost(i, j):
            x = costs[i][d] * values[j] - costs[j][d] * values[i]
            return -1 if x < 0 else (1 if x > 0 else 0)
        cost_orders.append(sorted(positive, key=cmp_to_key(compare_cost)))

    # High-gain and highly constraining choices tend to establish a good
    # incumbent early. The order affects speed only, never correctness.
    priorities = []
    for i in range(n):
        group_value, group_cost = totals(closure[i])
        scarcity = 1 + sum((Fraction(group_cost[d], budget[d] or 1)
                            for d in range(dim)), Fraction(0))
        priority = (Fraction(max(0, group_value), 1) / scarcity,
                    (excludes[i] | descendants[i]).bit_count(), -i)
        priorities.append(priority)
    choice_order = sorted(range(n), key=lambda i: priorities[i], reverse=True)

    best_value = value
    best_cost = cost
    best_mask = selected

    name_index = {name: i for i, name in enumerate(names)}

    def consider(mask, current_value, current_cost):
        nonlocal best_value, best_cost, best_mask
        if (current_value > best_value
                or (current_value == best_value and
                    (current_cost < best_cost or
                     (current_cost == best_cost and _lex_less(mask, best_mask))))):
            best_value, best_cost, best_mask = current_value, current_cost, mask

    def optimistic_lex(mask, available, current_value):
        needed = best_value - current_value
        latest_forced = mask.bit_length() - 1
        result = mask
        for i in range(n):
            bit = 1 << i
            if available & bit and (i < latest_forced or needed > 0):
                result |= bit
                needed -= max(0, values[i])
        return result

    def independent_remainder(available):
        todo = available
        while todo:
            bit = todo & -todo
            todo -= bit
            i = bit.bit_length() - 1
            if closure[i] & available != bit or excludes[i] & available:
                return False
        return True

    def finish_independent(mask, available):
        keep = mask | available
        local_names = []
        local_values = []
        local_costs = []
        local_required = 0
        todo = keep
        while todo:
            bit = todo & -todo
            todo -= bit
            i = bit.bit_length() - 1
            j = len(local_names)
            local_names.append(names[i])
            local_values.append(values[i])
            local_costs.append(costs[i])
            if mask & bit:
                local_required |= 1 << j
        answer = _solve_independent(local_names, local_values, local_costs,
                                    budget, local_required)
        if answer is not None:
            answer_mask = 0
            for name in answer["selected"]:
                answer_mask |= 1 << name_index[name]
            consider(answer_mask, answer["value"], tuple(answer["cost"]))

    def upper_bound(mask, forbidden, available, current_value, current_cost):
        raw = current_value
        todo = available
        while todo:
            bit = todo & -todo
            todo -= bit
            raw += max(0, values[bit.bit_length() - 1])
        if raw < best_value:
            return raw
        bound = raw
        for group, options, cache in components:
            forced = mask & group
            blocked = forbidden & group
            key = (forced, blocked)
            local_best = cache.get(key)
            if local_best is None:
                local_best = max(v for sub, v in options
                                 if sub & forced == forced and not sub & blocked)
                cache[key] = local_best
            optimistic_local = 0
            todo = forced
            while todo:
                bit = todo & -todo
                todo -= bit
                optimistic_local += values[bit.bit_length() - 1]
            todo = available & group
            while todo:
                bit = todo & -todo
                todo -= bit
                optimistic_local += max(0, values[bit.bit_length() - 1])
            bound += local_best - optimistic_local
        if bound < best_value:
            return bound
        remaining = available & positive_mask
        cover = current_value
        while remaining:
            bit = remaining & -remaining
            i = bit.bit_length() - 1
            clique = bit
            highest = values[i]
            candidates = (remaining ^ bit) & excludes[i]
            while candidates:
                bit = candidates & -candidates
                j = bit.bit_length() - 1
                clique |= bit
                if values[j] > highest:
                    highest = values[j]
                candidates = (candidates ^ bit) & excludes[j]
            remaining &= ~clique
            cover += highest
        if cover < bound:
            bound = cover
        if bound < best_value:
            return bound
        for d in range(dim):
            rem = budget[d] - current_cost[d]
            fractional = current_value
            for i in ratio_orders[d]:
                if not (available & (1 << i)):
                    continue
                c = costs[i][d]
                if c <= rem:
                    fractional += values[i]
                    rem -= c
                else:
                    fractional += values[i] * rem // c
                    break
            if fractional < bound:
                bound = fractional
            if bound < best_value:
                break
        return bound

    def optimistic_cost(available, current_value, current_cost):
        needed = max(0, best_value - current_value)
        result = []
        for d in range(dim):
            remaining = needed
            lower = current_cost[d]
            for i in cost_orders[d]:
                if not remaining:
                    break
                if not available & (1 << i):
                    continue
                if values[i] <= remaining:
                    lower += costs[i][d]
                    remaining -= values[i]
                else:
                    lower += (costs[i][d] * remaining + values[i] - 1) // values[i]
                    remaining = 0
            result.append(lower)
        return tuple(result)

    def search(mask, forbidden, current_value, current_cost):
        consider(mask, current_value, current_cost)
        available = full & ~(mask | forbidden)
        if not available:
            return
        bound = upper_bound(mask, forbidden, available,
                            current_value, current_cost)
        if bound < best_value:
            return
        if bound == best_value:
            lower_cost = optimistic_cost(available, current_value, current_cost)
            if lower_cost > best_cost:
                return
            if (lower_cost == best_cost and
                    not _lex_less(optimistic_lex(mask, available, current_value),
                                  best_mask)):
                return

        if available.bit_count() >= 12 and independent_remainder(available):
            finish_independent(mask, available)
            return

        i = next(i for i in choice_order if available & (1 << i))
        bit = 1 << i
        extra = closure[i] & ~mask
        if not (extra & forbidden):
            new_mask = mask | extra
            new_conflicts = conflict_bits(extra)
            if not (new_conflicts & new_mask):
                delta_value, delta_cost = totals(extra)
                new_cost = tuple(current_cost[d] + delta_cost[d]
                                 for d in range(dim))
                if all(new_cost[d] <= budget[d] for d in range(dim)):
                    new_forbidden = forbidden | ban_descendants(new_conflicts)
                    if not (new_forbidden & new_mask):
                        search(new_mask, new_forbidden,
                               current_value + delta_value, new_cost)
        search(mask, forbidden | descendants[i], current_value, current_cost)

    search(selected, banned, value, cost)
    return {"selected": [names[i] for i in range(n) if best_mask & (1 << i)],
            "value": best_value, "cost": list(best_cost)}


def solve(projects, budget, required=()):
    """Return the globally best feasible portfolio, or ``None``."""
    (names, values, costs, requires, excludes, closure,
     required_mask) = _validated(projects, budget, required)
    if not names:
        return {"selected": [], "value": 0, "cost": [0] * len(budget)}
    if not any(requires) and not any(excludes):
        return _solve_independent(names, values, costs, budget, required_mask)
    return _solve_constrained(names, values, costs, requires, excludes,
                              closure, budget, required_mask)
