"""Exact solver for the constrained portfolio problem."""

from bisect import bisect_left, bisect_right
from functools import cmp_to_key


_KEYS = {"id", "value", "cost", "requires", "excludes"}


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _validate(projects, budget, required):
    if not isinstance(projects, list) or len(projects) > 32:
        raise ValueError("projects must be a list containing at most 32 items")
    if (not isinstance(budget, list) or not 1 <= len(budget) <= 3 or
            any(not _integer(x) or x < 0 for x in budget)):
        raise ValueError("invalid budget")
    if not isinstance(required, (list, tuple)):
        raise ValueError("required must be a list or tuple")

    rows = []
    ids = set()
    dimensions = len(budget)
    for project in projects:
        if not isinstance(project, dict) or set(project) != _KEYS:
            raise ValueError("each project must have exactly the documented keys")
        name = project["id"]
        value = project["value"]
        cost = project["cost"]
        requires = project["requires"]
        excludes = project["excludes"]
        if not isinstance(name, str) or not name or name in ids:
            raise ValueError("invalid or duplicate project id")
        ids.add(name)
        if not _integer(value):
            raise ValueError("invalid project value")
        if (not isinstance(cost, list) or len(cost) != dimensions or
                any(not _integer(x) or x < 0 for x in cost)):
            raise ValueError("invalid project cost")
        if not isinstance(requires, list) or not isinstance(excludes, list):
            raise ValueError("requires and excludes must be lists")
        rows.append((name, value, tuple(cost), tuple(requires), tuple(excludes)))

    index = {row[0]: i for i, row in enumerate(rows)}
    for name, _, _, requires, excludes in rows:
        for references in (requires, excludes):
            try:
                distinct = len(set(references)) == len(references)
            except TypeError:
                distinct = False
            if not distinct:
                raise ValueError("duplicate reference")
            for other in references:
                if not isinstance(other, str) or other not in index or other == name:
                    raise ValueError("invalid project reference")

    try:
        required_distinct = len(set(required)) == len(required)
    except TypeError:
        required_distinct = False
    if (not required_distinct or
            any(not isinstance(x, str) or x not in index for x in required)):
        raise ValueError("invalid required id")

    colour = [0] * len(rows)

    def visit(i):
        if colour[i] == 1:
            raise ValueError("dependency cycle")
        if colour[i] == 2:
            return
        colour[i] = 1
        for name in rows[i][3]:
            visit(index[name])
        colour[i] = 2

    for i in range(len(rows)):
        visit(i)
    return rows, index, tuple(budget), tuple(required)


def _enumerate_half(rows, positions, forced, budget):
    dims = len(budget)
    states = [(0, 0, (0,) * dims)]
    for local, position in enumerate(positions):
        _, value, cost, _, _ = rows[position]
        bit = 1 << local
        additions = []
        for mask, old_value, old_cost in states:
            new_cost = tuple(old_cost[d] + cost[d] for d in range(dims))
            if all(new_cost[d] <= budget[d] for d in range(dims)):
                additions.append((mask | bit, old_value + value, new_cost))
        states = additions if position in forced else states + additions
    return states


def _solve_independent(rows, index, budget, required):
    """Meet in the middle plus offline orthogonal maximum queries."""
    order = sorted(range(len(rows)), key=lambda i: rows[i][0])
    middle = len(order) // 2
    left_pos, right_pos = order[:middle], order[middle:]
    forced = {index[name] for name in required}
    left = _enumerate_half(rows, left_pos, forced, budget)
    right = _enumerate_half(rows, right_pos, forced, budget)
    if not left or not right:
        return None
    dims = len(budget)
    right_names = [rows[i][0] for i in right_pos]
    left_names = [rows[i][0] for i in left_pos]
    right_tuple_cache = {}
    left_tuple_cache = {}

    def names(mask, source, cache):
        answer = cache.get(mask)
        if answer is None:
            answer = tuple(source[i] for i in range(len(source)) if mask & (1 << i))
            cache[mask] = answer
        return answer

    def better_right(a, b):
        if b is None:
            return True
        ra, rb = right[a], right[b]
        if ra[1] != rb[1]:
            return ra[1] > rb[1]
        if ra[2] != rb[2]:
            return ra[2] < rb[2]
        return names(ra[0], right_names, right_tuple_cache) < names(
            rb[0], right_names, right_tuple_cache)

    def keep(best, candidate):
        return candidate if candidate is not None and better_right(candidate, best) else best

    answers = [None] * len(left)
    queries = []
    for q, (_, _, cost) in enumerate(left):
        cap = tuple(budget[d] - cost[d] for d in range(dims))
        if all(x >= 0 for x in cap):
            queries.append((cap, q))

    if dims == 1:
        points = sorted(range(len(right)), key=lambda i: right[i][2][0])
        queries.sort(key=lambda x: x[0][0])
        p = 0
        best = None
        for cap, q in queries:
            while p < len(points) and right[points[p]][2][0] <= cap[0]:
                best = keep(best, points[p])
                p += 1
            answers[q] = best
    elif dims == 2:
        ys = sorted({record[2][1] for record in right})
        tree = [None] * (len(ys) + 1)

        def update(y, candidate):
            k = bisect_left(ys, y) + 1
            while k < len(tree):
                tree[k] = keep(tree[k], candidate)
                k += k & -k

        def query(y):
            k = bisect_right(ys, y)
            answer = None
            while k:
                answer = keep(answer, tree[k])
                k -= k & -k
            return answer

        points = sorted(range(len(right)), key=lambda i: right[i][2][0])
        queries.sort(key=lambda x: x[0][0])
        p = 0
        for cap, q in queries:
            while p < len(points) and right[points[p]][2][0] <= cap[0]:
                update(right[points[p]][2][1], points[p])
                p += 1
            answers[q] = query(cap[1])
    else:
        ys = sorted({record[2][1] for record in right})
        node_z = [[] for _ in range(len(ys) + 1)]
        point_y = []
        for record in right:
            k = bisect_left(ys, record[2][1]) + 1
            point_y.append(k)
            while k < len(node_z):
                node_z[k].append(record[2][2])
                k += k & -k
        node_z = [sorted(set(values)) for values in node_z]
        trees = [[None] * (len(values) + 1) for values in node_z]

        def update3(point):
            y = point_y[point]
            z = right[point][2][2]
            while y < len(trees):
                k = bisect_left(node_z[y], z) + 1
                tree = trees[y]
                while k < len(tree):
                    tree[k] = keep(tree[k], point)
                    k += k & -k
                y += y & -y

        def query3(y_cap, z_cap):
            y = bisect_right(ys, y_cap)
            answer = None
            while y:
                k = bisect_right(node_z[y], z_cap)
                tree = trees[y]
                while k:
                    answer = keep(answer, tree[k])
                    k -= k & -k
                y -= y & -y
            return answer

        points = sorted(range(len(right)), key=lambda i: right[i][2][0])
        queries.sort(key=lambda x: x[0][0])
        p = 0
        for cap, q in queries:
            while p < len(points) and right[points[p]][2][0] <= cap[0]:
                update3(points[p])
                p += 1
            answers[q] = query3(cap[1], cap[2])

    best = None
    for li, ri in enumerate(answers):
        if ri is None:
            continue
        lm, lv, lc = left[li]
        rm, rv, rc = right[ri]
        value = lv + rv
        cost = tuple(lc[d] + rc[d] for d in range(dims))
        selected = (names(lm, left_names, left_tuple_cache) +
                    names(rm, right_names, right_tuple_cache))
        candidate = (value, cost, selected)
        if (best is None or value > best[0] or
                (value == best[0] and (cost < best[1] or
                 (cost == best[1] and selected < best[2])))):
            best = candidate
    if best is None:
        return None
    return {"selected": list(best[2]), "value": best[0], "cost": list(best[1])}


_FALLBACK = object()


def _solve_by_components(rows, index, budget, required):
    """Combine exact option tables for disconnected constraint components."""
    n, dims = len(rows), len(budget)
    adjacency = [0] * n
    direct = [0] * n
    conflicts = [0] * n
    for i, row in enumerate(rows):
        for name in row[3]:
            j = index[name]
            direct[i] |= 1 << j
            adjacency[i] |= 1 << j
            adjacency[j] |= 1 << i
        for name in row[4]:
            j = index[name]
            conflicts[i] |= 1 << j
            conflicts[j] |= 1 << i
            adjacency[i] |= 1 << j
            adjacency[j] |= 1 << i

    components = []
    unseen = (1 << n) - 1
    while unseen:
        seed = unseen & -unseen
        component = 0
        fringe = seed
        while fringe:
            bit = fringe & -fringe
            fringe -= bit
            if component & bit:
                continue
            component |= bit
            i = bit.bit_length() - 1
            fringe |= adjacency[i] & ~component
        unseen &= ~component
        components.append([i for i in range(n) if component & (1 << i)])

    # Large connected components are better handled by propagation and bounds.
    if any(len(component) > 18 for component in components):
        return _FALLBACK
    # Process components from lexically high IDs to low IDs.  This makes a
    # lexical winner at an intermediate DP state stable when later IDs are
    # merged.  Interleaved component ID ranges do not have that property.
    components.sort(key=lambda component: min(rows[i][0] for i in component))
    if any(max(rows[i][0] for i in components[k]) >=
           min(rows[i][0] for i in components[k + 1])
           for k in range(len(components) - 1)):
        return _FALLBACK
    components.reverse()
    required_mask = sum(1 << index[name] for name in required)
    option_tables = []
    for component in components:
        size = len(component)
        global_bits = [1 << i for i in component]
        local_of = {project: local for local, project in enumerate(component)}
        local_required = sum(1 << local_of[i] for i in component
                             if required_mask & (1 << i))
        local_direct = []
        local_conflict = []
        for i in component:
            local_direct.append(sum(1 << local_of[j] for j in component
                                    if direct[i] & (1 << j)))
            local_conflict.append(sum(1 << local_of[j] for j in component
                                      if conflicts[i] & (1 << j)))
        options = []
        for local_mask in range(1 << size):
            if local_mask & local_required != local_required:
                continue
            valid = True
            for local in range(size):
                if local_mask & (1 << local):
                    if (local_direct[local] & ~local_mask or
                            local_conflict[local] & local_mask):
                        valid = False
                        break
            if not valid:
                continue
            global_mask = sum(global_bits[local] for local in range(size)
                              if local_mask & (1 << local))
            value = sum(rows[i][1] for i in component
                        if global_mask & (1 << i))
            cost = tuple(sum(rows[i][2][d] for i in component
                             if global_mask & (1 << i)) for d in range(dims))
            if all(cost[d] <= budget[d] for d in range(dims)):
                options.append((value, cost, global_mask))
        if not options:
            return None
        option_tables.append(options)

    id_order = sorted(range(n), key=lambda i: rows[i][0])
    name_cache = {}

    def names(mask):
        answer = name_cache.get(mask)
        if answer is None:
            answer = tuple(rows[i][0] for i in id_order if mask & (1 << i))
            name_cache[mask] = answer
        return answer

    states = {(0,) * dims: (0, 0)}  # cost -> (value, global mask)
    for options in option_tables:
        combined = {}
        for old_cost, (old_value, old_mask) in states.items():
            for value, cost, mask in options:
                new_cost = tuple(old_cost[d] + cost[d] for d in range(dims))
                if any(new_cost[d] > budget[d] for d in range(dims)):
                    continue
                candidate = (old_value + value, old_mask | mask)
                previous = combined.get(new_cost)
                if (previous is None or candidate[0] > previous[0] or
                        (candidate[0] == previous[0] and
                         names(candidate[1]) < names(previous[1]))):
                    combined[new_cost] = candidate
        if not combined:
            return None
        # In one dimension, discard states dominated in both cost and value.
        if dims == 1:
            frontier = {}
            highest = None
            for cost in sorted(combined):
                candidate = combined[cost]
                if highest is None or candidate[0] > highest:
                    frontier[cost] = candidate
                    highest = candidate[0]
            combined = frontier
        # Avoid turning a difficult multidimensional instance into an
        # unbounded table; the branch-and-bound solver remains exact.
        if len(combined) > 400000:
            return _FALLBACK
        states = combined

    best = None
    for cost, (value, mask) in states.items():
        candidate = (value, cost, names(mask))
        if (best is None or value > best[0] or
                (value == best[0] and (cost < best[1] or
                 (cost == best[1] and candidate[2] < best[2])))):
            best = candidate
    return {"selected": list(best[2]), "value": best[0], "cost": list(best[1])}


def _solve_constrained(rows, index, budget, required):
    n, dims = len(rows), len(budget)
    all_mask = (1 << n) - 1
    values = [row[1] for row in rows]
    costs = [row[2] for row in rows]
    direct = [sum(1 << index[name] for name in row[3]) for row in rows]
    conflict = [0] * n
    for i, row in enumerate(rows):
        for name in row[4]:
            j = index[name]
            conflict[i] |= 1 << j
            conflict[j] |= 1 << i

    closure = [0] * n

    def close(i):
        if closure[i]:
            return closure[i]
        answer = 1 << i
        mask = direct[i]
        while mask:
            bit = mask & -mask
            answer |= close(bit.bit_length() - 1)
            mask -= bit
        closure[i] = answer
        return answer

    for i in range(n):
        close(i)
    dependents = [0] * n
    for i in range(n):
        mask = closure[i]
        while mask:
            bit = mask & -mask
            dependents[bit.bit_length() - 1] |= 1 << i
            mask -= bit

    def has_conflict(mask):
        work = mask
        while work:
            bit = work & -work
            if conflict[bit.bit_length() - 1] & mask:
                return True
            work -= bit
        return False

    impossible = 0
    for i in range(n):
        if has_conflict(closure[i]):
            impossible |= dependents[i]

    selected = 0
    for name in required:
        selected |= closure[index[name]]
    if has_conflict(selected) or selected & impossible:
        return None

    def totals(mask):
        value = 0
        cost = [0] * dims
        while mask:
            bit = mask & -mask
            i = bit.bit_length() - 1
            value += values[i]
            for d in range(dims):
                cost[d] += costs[i][d]
            mask -= bit
        return value, tuple(cost)

    start_value, start_cost = totals(selected)
    if any(start_cost[d] > budget[d] for d in range(dims)):
        return None

    forbidden = impossible
    work = selected
    while work:
        bit = work & -work
        i = bit.bit_length() - 1
        foes = conflict[i]
        while foes:
            foe = foes & -foes
            forbidden |= dependents[foe.bit_length() - 1]
            foes -= foe
        work -= bit
    if selected & forbidden:
        return None

    ratio_orders = []
    for d in range(dims):
        def compare(i, j, dim=d):
            ci, cj = costs[i][dim], costs[j][dim]
            if ci == 0 or cj == 0:
                return -1 if ci == 0 and cj != 0 else (1 if cj == 0 and ci != 0 else 0)
            lhs, rhs = values[i] * cj, values[j] * ci
            return -1 if lhs > rhs else (1 if lhs < rhs else 0)
        ratio_orders.append(sorted((i for i in range(n) if values[i] > 0),
                                   key=cmp_to_key(compare)))

    id_order = sorted(range(n), key=lambda i: rows[i][0])
    tuple_cache = {}

    def selected_names(mask):
        answer = tuple_cache.get(mask)
        if answer is None:
            answer = tuple(rows[i][0] for i in id_order if mask & (1 << i))
            tuple_cache[mask] = answer
        return answer

    best_value = None
    best_cost = None
    best_names = None
    seen = set()

    def consider(mask, value, cost):
        nonlocal best_value, best_cost, best_names
        if (best_value is None or value > best_value or
                (value == best_value and cost < best_cost)):
            best_value, best_cost, best_names = value, cost, selected_names(mask)
        elif value == best_value and cost == best_cost:
            candidate_names = selected_names(mask)
            if candidate_names < best_names:
                best_names = candidate_names

    def upper_bound(available, value, cost):
        upper = value + sum(values[i] for i in range(n)
                            if available & (1 << i) and values[i] > 0)
        for d in range(dims):
            remaining = budget[d] - cost[d]
            bound = value
            for i in ratio_orders[d]:
                if not available & (1 << i):
                    continue
                c = costs[i][d]
                if c == 0:
                    bound += values[i]
                elif c <= remaining:
                    bound += values[i]
                    remaining -= c
                else:
                    bound += (values[i] * remaining) // c
                    break
            upper = min(upper, bound)
        return upper

    def dfs(mask, banned, value, cost):
        state = (mask, banned)
        if state in seen:
            return
        seen.add(state)
        consider(mask, value, cost)
        available = all_mask & ~(mask | banned)
        if not available:
            return
        ub = upper_bound(available, value, cost)
        if ub < best_value or (ub == best_value and cost > best_cost):
            return
        if ub == best_value and cost == best_cost:
            # Among arbitrary supersets of the current selection, this is a
            # lexical lower bound: adding every available ID below the last
            # selected ID helps, while adding an ID after it cannot help.
            current_names = selected_names(mask)
            if not current_names:
                lexical_lower = ()
            else:
                last = current_names[-1]
                lexical_lower = tuple(rows[i][0] for i in id_order
                                      if (mask | available) & (1 << i)
                                      and (mask & (1 << i) or rows[i][0] < last))
            if lexical_lower >= best_names:
                return

        choice = None
        choice_key = None
        work = available
        while work:
            bit = work & -work
            i = bit.bit_length() - 1
            marginal = closure[i] & ~mask
            gain = sum(values[j] for j in range(n) if marginal & (1 << j))
            weight = sum(costs[j][d] for j in range(n) for d in range(dims)
                         if marginal & (1 << j))
            degree = (conflict[i] | direct[i] | dependents[i]).bit_count()
            key = (gain > 0, gain * 1000000 // (weight + 1), degree, abs(gain))
            if choice_key is None or key > choice_key:
                choice, choice_key = i, key
            work -= bit

        marginal = closure[choice] & ~mask
        gain, added_cost = totals(marginal)
        new_cost = tuple(cost[d] + added_cost[d] for d in range(dims))

        def include_branch():
            if (marginal & banned or has_conflict(mask | marginal) or
                    any(new_cost[d] > budget[d] for d in range(dims))):
                return
            new_mask = mask | marginal
            new_banned = banned
            additions = marginal
            while additions:
                bit = additions & -additions
                foes = conflict[bit.bit_length() - 1]
                while foes:
                    foe = foes & -foes
                    new_banned |= dependents[foe.bit_length() - 1]
                    foes -= foe
                additions -= bit
            if not new_mask & new_banned:
                dfs(new_mask, new_banned, value + gain, new_cost)

        def exclude_branch():
            dfs(mask, banned | dependents[choice], value, cost)

        if gain > 0:
            include_branch()
            exclude_branch()
        else:
            exclude_branch()
            include_branch()

    dfs(selected, forbidden, start_value, start_cost)
    return {"selected": list(best_names), "value": best_value, "cost": list(best_cost)}


def solve(projects, budget, required=()):
    """Return the globally optimal feasible portfolio, or ``None``."""
    rows, index, budget, required = _validate(projects, budget, required)
    if all(not row[3] and not row[4] for row in rows):
        return _solve_independent(rows, index, budget, required)
    component_answer = _solve_by_components(rows, index, budget, required)
    if component_answer is not _FALLBACK:
        return component_answer
    return _solve_constrained(rows, index, budget, required)
