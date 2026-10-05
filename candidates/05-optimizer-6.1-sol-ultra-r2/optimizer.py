"""Exact portfolio optimization using a bounded meet-in-the-middle search."""

from bisect import bisect_left, bisect_right


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _validate(projects, budget, required):
    if not isinstance(projects, list) or len(projects) > 32:
        raise ValueError("projects must be a list of at most 32 dictionaries")
    if (not isinstance(budget, list) or not 1 <= len(budget) <= 3
            or any(not _integer(x) or x < 0 for x in budget)):
        raise ValueError("budget must contain one to three nonnegative integers")
    if not isinstance(required, (list, tuple)):
        raise ValueError("required must be a list or tuple")

    keys = {"id", "value", "cost", "requires", "excludes"}
    known = set()
    for project in projects:
        if not isinstance(project, dict) or project.keys() != keys:
            raise ValueError("each project must have exactly the prescribed keys")
        name = project["id"]
        if not isinstance(name, str) or not name or name in known:
            raise ValueError("project IDs must be unique nonempty strings")
        known.add(name)
        if not _integer(project["value"]):
            raise ValueError("project values must be integers, excluding bool")
        costs = project["cost"]
        if (not isinstance(costs, list) or len(costs) != len(budget)
                or any(not _integer(x) or x < 0 for x in costs)):
            raise ValueError("project costs must match the budget dimensions")
        for field in ("requires", "excludes"):
            references = project[field]
            if not isinstance(references, list):
                raise ValueError("project references must be lists")
            seen = set()
            for reference in references:
                if (not isinstance(reference, str) or not reference
                        or reference == name or reference in seen):
                    raise ValueError("invalid or duplicate project reference")
                seen.add(reference)

    for project in projects:
        for field in ("requires", "excludes"):
            if any(reference not in known for reference in project[field]):
                raise ValueError("unknown project reference")
    seen = set()
    for name in required:
        if not isinstance(name, str) or name not in known or name in seen:
            raise ValueError("invalid or duplicate required ID")
        seen.add(name)

    ordered = sorted(projects, key=lambda p: p["id"])
    ids = [p["id"] for p in ordered]
    index = {name: i for i, name in enumerate(ids)}
    needs = [sum(1 << index[x] for x in p["requires"]) for p in ordered]
    excludes = [0] * len(ordered)
    for i, project in enumerate(ordered):
        for name in project["excludes"]:
            j = index[name]
            excludes[i] |= 1 << j
            excludes[j] |= 1 << i

    # Check the entire graph, including projects that cannot fit any budget.
    closure = [0] * len(ordered)
    visiting = [False] * len(ordered)

    def visit(i):
        if visiting[i]:
            raise ValueError("the dependency graph must be acyclic")
        if closure[i]:
            return closure[i]
        visiting[i] = True
        result = 1 << i
        pending = needs[i]
        while pending:
            bit = pending & -pending
            pending ^= bit
            result |= visit(bit.bit_length() - 1)
        visiting[i] = False
        closure[i] = result
        return result

    for i in range(len(ordered)):
        visit(i)
    fixed = 0
    for name in required:
        fixed |= closure[index[name]]
    return (ids, [p["value"] for p in ordered],
            [tuple(p["cost"]) for p in ordered], needs, excludes, closure, fixed)


def _lex_less(a, b, marker=0):
    """Compare sorted ID tuples encoded as masks, with a common last ID.

    At the first differing ID, including it wins unless the other tuple ends
    there. A common prefix has no effect; a common later ID prevents that end.
    Thus only the highest common ID (marker) is needed for comparisons of the
    right halves with a fixed left half.
    """
    difference = a ^ b
    if not difference:
        return False
    first = difference & -difference
    if a & first:
        return (b | marker) > first
    return (a | marker) < first


def _partition(available, neighbors):
    """Keep whole constraint components together whenever they fit in halves."""
    components = []
    remaining = available
    while remaining:
        component = 0
        frontier = remaining & -remaining
        while frontier:
            bit = frontier & -frontier
            frontier ^= bit
            if component & bit:
                continue
            component |= bit
            frontier |= neighbors[bit.bit_length() - 1] & available & ~component
        components.append(component)
        remaining &= ~component

    count = available.bit_count()
    target = count // 2
    reachable = {0: 0}
    for component in components:
        size = component.bit_count()
        for old_size, old_mask in list(reachable.items()):
            if old_size + size <= 16:
                reachable.setdefault(old_size + size, old_mask | component)
    choices = [size for size in reachable if count - size <= 16]
    if choices:
        size = min(choices, key=lambda s: (abs(s - target), s))
        return reachable[size], available ^ reachable[size]

    # A component must be cut. A graph traversal and improving balanced swaps
    # reduce cross constraints; this affects speed only, never correctness.
    order = []
    visited = 0
    for component in components:
        stack = [component & -component]
        while stack:
            bit = stack.pop()
            if visited & bit:
                continue
            visited |= bit
            order.append(bit)
            adjacent = neighbors[bit.bit_length() - 1] & component & ~visited
            while adjacent:
                other = adjacent & -adjacent
                adjacent ^= other
                stack.append(other)
    left = sum(order[:target])
    right = available ^ left
    for _ in range(count):
        improvement = 0
        swap = None
        for i in range(len(neighbors)):
            ib = 1 << i
            if not left & ib:
                continue
            delta_i = ((neighbors[i] & left).bit_count()
                       - (neighbors[i] & right).bit_count())
            for j in range(len(neighbors)):
                jb = 1 << j
                if not right & jb:
                    continue
                delta = (delta_i + (neighbors[j] & right).bit_count()
                         - (neighbors[j] & left).bit_count()
                         + 2 * bool(neighbors[i] & jb))
                if delta < improvement:
                    improvement, swap = delta, ib | jb
        if swap is None:
            break
        left ^= swap
        right ^= swap
    return left, right


def _enumerate(side, other, values, costs, closure, excludes, limits):
    indices = [i for i in range(len(values)) if side & (1 << i)]
    dimensions = len(limits)
    records = []

    def walk(position, selected, omitted, needed, forbidden, value, cost):
        if position == len(indices):
            records.append((selected, value, cost, needed & other, forbidden & other))
            return
        i = indices[position]
        bit = 1 << i
        if not needed & bit:
            walk(position + 1, selected, omitted | bit, needed, forbidden, value, cost)
        if forbidden & bit or closure[i] & omitted:
            return
        new_cost = tuple(cost[d] + costs[i][d] for d in range(dimensions))
        if any(new_cost[d] > limits[d] for d in range(dimensions)):
            return
        walk(position + 1, selected | bit, omitted, needed | closure[i],
             forbidden | excludes[i], value + values[i], new_cost)

    walk(0, 0, 0, 0, 0, 0, (0,) * dimensions)
    return records


class _Node:
    __slots__ = ("minimum", "union", "intersection", "need_union", "need_intersection",
                 "forbid_union", "forbid_intersection", "best", "value", "cost",
                 "children", "records")


def _build_tree(records, limits, markers, cross_selected, depth=0):
    """Spatial/constraint index with exact objective bounds for each subtree."""
    node = _Node()
    dimensions = len(limits)
    minimum = list(records[0][2])
    maximum = minimum[:]
    union = need_union = forbid_union = 0
    intersection, need_intersection, forbid_intersection = (
        records[0][0], records[0][3], records[0][4])
    for mask, value, cost, need, forbid in records:
        union |= mask
        intersection &= mask
        need_union |= need
        need_intersection &= need
        forbid_union |= forbid
        forbid_intersection &= forbid
        for d in range(dimensions):
            if cost[d] < minimum[d]:
                minimum[d] = cost[d]
            if cost[d] > maximum[d]:
                maximum[d] = cost[d]
    node.minimum = tuple(minimum)
    node.union, node.intersection = union, intersection
    node.need_union, node.need_intersection = need_union, need_intersection
    node.forbid_union, node.forbid_intersection = forbid_union, forbid_intersection

    axis = 0
    for d in range(1, dimensions):
        if ((maximum[d] - minimum[d]) * max(1, limits[axis])
                > (maximum[axis] - minimum[axis]) * max(1, limits[d])):
            axis = d
    spread = maximum[axis] - minimum[axis]
    varying = ((union ^ intersection) & cross_selected,
               need_union ^ need_intersection, forbid_union ^ forbid_intersection)
    structural = any(varying)
    if len(records) <= 12 or (not spread and not structural):
        best_value = max(r[1] for r in records)
        best_cost = min(r[2] for r in records if r[1] == best_value)
        ties = [r for r in records if r[1] == best_value and r[2] == best_cost]
        best = []
        for marker in markers:
            winner = ties[0]
            for record in ties[1:]:
                if _lex_less(record[0], winner[0], marker):
                    winner = record
            best.append(winner)
        node.best = tuple(best)
        node.value, node.cost = best_value, best_cost
        node.children = None
        node.records = records
        return node

    if structural and (not spread or depth % (dimensions + 1) == 0):
        field = next(i for i in range(3) if varying[i])
        bit = varying[field] & -varying[field]
        record_field = (0, 3, 4)[field]
        first = [r for r in records if not r[record_field] & bit]
        second = [r for r in records if r[record_field] & bit]
    else:
        records.sort(key=lambda r: r[2][axis])
        middle = len(records) // 2
        first, second = records[:middle], records[middle:]
    a = _build_tree(first, limits, markers, cross_selected, depth + 1)
    b = _build_tree(second, limits, markers, cross_selected, depth + 1)
    node.children, node.records = (a, b), None
    if a.value > b.value or (a.value == b.value and a.cost < b.cost):
        node.best, node.value, node.cost = a.best, a.value, a.cost
    elif b.value > a.value or (a.value == b.value and b.cost < a.cost):
        node.best, node.value, node.cost = b.best, b.value, b.cost
    else:
        node.value, node.cost = a.value, a.cost
        node.best = tuple(ar if _lex_less(ar[0], br[0], marker) else br
                          for ar, br, marker in zip(a.best, b.best, markers))
    return node


def _combine_without_cross_constraints(left, right, limits, fixed, marker):
    """Offline orthant maximum queries, using compressed Fenwick trees.

    Sweep the first cost coordinate. The remaining zero, one, or two cost
    coordinates need respectively a scalar, a Fenwick tree, or a compressed
    Fenwick tree of Fenwick trees. A rank encodes the complete right-half
    objective, so every prefix query returns its exact best feasible record.
    """
    dimensions = len(limits)

    def rank_key(record):
        selected = record[0] | marker
        indices = []
        while selected:
            bit = selected & -selected
            selected ^= bit
            indices.append(bit.bit_length() - 1)
        return -record[1], record[2], tuple(indices)

    ranked = sorted(right, key=rank_key)
    infinity = len(ranked)
    points = sorted(range(infinity), key=lambda rank: ranked[rank][2][0])
    current = infinity
    if dimensions >= 2:
        xs = sorted({r[2][1] for r in ranked})
        positions = {x: i + 1 for i, x in enumerate(xs)}
        width = len(xs)
        if dimensions == 2:
            fenwick = [infinity] * (width + 1)
        else:
            ys = [[] for _ in range(width + 1)]
            for record in ranked:
                x = positions[record[2][1]]
                while x <= width:
                    ys[x].append(record[2][2])
                    x += x & -x
            ys = [sorted(set(coordinates)) for coordinates in ys]
            fenwick = [[infinity] * (len(coordinates) + 1) for coordinates in ys]

    def insert(rank):
        nonlocal current
        if dimensions == 1:
            if rank < current:
                current = rank
            return
        cost = ranked[rank][2]
        x = positions[cost[1]]
        if dimensions == 2:
            while x <= width:
                if rank < fenwick[x]:
                    fenwick[x] = rank
                x += x & -x
        else:
            while x <= width:
                tree = fenwick[x]
                y = bisect_left(ys[x], cost[2]) + 1
                while y < len(tree):
                    if rank < tree[y]:
                        tree[y] = rank
                    y += y & -y
                x += x & -x

    def query(room):
        if dimensions == 1:
            return current
        result = infinity
        x = bisect_right(xs, room[1])
        if dimensions == 2:
            while x:
                if fenwick[x] < result:
                    result = fenwick[x]
                x -= x & -x
        else:
            while x:
                tree = fenwick[x]
                y = bisect_right(ys[x], room[2])
                while y:
                    if tree[y] < result:
                        result = tree[y]
                    y -= y & -y
                x -= x & -x
        return result

    best_value, best_cost, best_mask = 0, (0,) * dimensions, fixed
    cursor = 0
    for lm, lv, lc, _, _ in sorted(left, key=lambda r: -r[2][0]):
        room = tuple(limits[d] - lc[d] for d in range(dimensions))
        while cursor < infinity and ranked[points[cursor]][2][0] <= room[0]:
            insert(points[cursor])
            cursor += 1
        rank = query(room)
        if rank == infinity:
            continue
        rm, rv, rc, _, _ = ranked[rank]
        value = lv + rv
        cost = tuple(lc[d] + rc[d] for d in range(dimensions))
        mask = fixed | lm | rm
        if (value > best_value or (value == best_value
                and (cost < best_cost or (cost == best_cost and _lex_less(mask, best_mask))))):
            best_value, best_cost, best_mask = value, cost, mask
    return best_value, best_cost, best_mask


def solve(projects, budget, required=()):
    """Return the exact best feasible portfolio, or None if required is infeasible.

    Full validation precedes any feasibility shortcut. Required dependencies
    are fixed first; each remaining feasible portfolio is a compatible pair
    of half portfolios. Range queries or sound subtree bounds find the best
    pair, including both tie breakers. General worst-case time is exponential;
    each half contains at most 16 projects and is enumerated only once.
    """
    ids, values, costs, needs, excludes, closure, fixed = _validate(
        projects, budget, required)
    n, dimensions = len(ids), len(budget)

    def totals(mask):
        value = 0
        cost = [0] * dimensions
        forbidden = 0
        while mask:
            bit = mask & -mask
            mask ^= bit
            i = bit.bit_length() - 1
            value += values[i]
            forbidden |= excludes[i]
            for d in range(dimensions):
                cost[d] += costs[i][d]
        return value, tuple(cost), forbidden

    fixed_value, fixed_cost, fixed_forbidden = totals(fixed)
    if fixed & fixed_forbidden or any(fixed_cost[d] > budget[d] for d in range(dimensions)):
        return None
    limits = tuple(budget[d] - fixed_cost[d] for d in range(dimensions))
    available = 0
    for i in range(n):
        bit = 1 << i
        if fixed & bit or closure[i] & fixed_forbidden:
            continue
        _, cost, forbidden = totals(closure[i] & ~fixed)
        if (forbidden & (closure[i] | fixed)
                or any(cost[d] > limits[d] for d in range(dimensions))):
            continue
        available |= bit

    if not available:
        return {"selected": [ids[i] for i in range(n) if fixed & (1 << i)],
                "value": fixed_value, "cost": list(fixed_cost)}

    neighbors = [needs[i] | excludes[i] for i in range(n)]
    for i in range(n):
        pending = needs[i]
        while pending:
            bit = pending & -pending
            pending ^= bit
            neighbors[bit.bit_length() - 1] |= 1 << i
    left_mask, right_mask = _partition(available, neighbors)
    left = _enumerate(left_mask, right_mask, values, costs, closure, excludes, limits)
    right = _enumerate(right_mask, left_mask, values, costs, closure, excludes, limits)

    # For right-half ID ties only the last fixed/left ID affects tuple ordering.
    lengths = sorted({(record[0] | fixed).bit_length() for record in left})
    contexts, prefixes, markers = {}, {}, []
    for length in lengths:
        marker = 1 << (length - 1) if length else 0
        # Marker positions with no right-hand IDs between them induce the same
        # ordering. In particular, an ID-ordered split needs only one context.
        prefix = right_mask & ((marker << 1) - 1) if marker else 0
        if prefix not in prefixes:
            prefixes[prefix] = len(markers)
            markers.append(marker)
        contexts[length] = prefixes[prefix]
    if (len(markers) == 1 and all(not (r[3] or r[4]) for r in left)
            and all(not (r[3] or r[4]) for r in right)):
        value, cost, mask = _combine_without_cross_constraints(
            left, right, limits, fixed, markers[0])
        return {"selected": [ids[i] for i in range(n) if mask & (1 << i)],
                "value": fixed_value + value,
                "cost": [fixed_cost[d] + cost[d] for d in range(dimensions)]}
    cross_selected = 0
    for i in range(n):
        if left_mask & (1 << i):
            cross_selected |= (closure[i] | excludes[i]) & right_mask
    tree = _build_tree(right, limits, markers, cross_selected)
    left.sort(key=lambda r: (-r[1], r[2]))
    best_value, best_cost, best_mask = 0, (0,) * dimensions, fixed

    for lm, lv, lc, ln, lf in left:
        context = contexts[(lm | fixed).bit_length()]
        room = tuple(limits[d] - lc[d] for d in range(dimensions))
        complement = ~lm
        stack = [tree]
        while stack:
            node = stack.pop()
            if lv + node.value < best_value:
                continue
            if (any(node.minimum[d] > room[d] for d in range(dimensions))
                    or ln & ~node.union or lf & node.intersection
                    or node.need_intersection & complement
                    or node.forbid_intersection & lm):
                continue
            bound_cost = tuple(lc[d] + node.cost[d] for d in range(dimensions))
            candidate = node.best[context]
            combined_mask = fixed | lm | candidate[0]
            if lv + node.value == best_value:
                if bound_cost > best_cost:
                    continue
                if bound_cost == best_cost and not _lex_less(combined_mask, best_mask):
                    continue
            rm, rv, rc, rn, rf = candidate
            if (not (ln & ~rm or lf & rm or rn & complement or rf & lm)
                    and all(rc[d] <= room[d] for d in range(dimensions))):
                # This is the subtree's best unconstrained answer for this left
                # half. Once feasible, no other member can improve it.
                best_value, best_cost, best_mask = lv + rv, bound_cost, combined_mask
                continue
            if node.children is not None:
                a, b = node.children
                if (a.value > b.value or (a.value == b.value and a.cost < b.cost)
                        or (a.value == b.value and a.cost == b.cost
                            and _lex_less(a.best[context][0], b.best[context][0], markers[context]))):
                    stack.extend((b, a))
                else:
                    stack.extend((a, b))
                continue
            for rm, rv, rc, rn, rf in node.records:
                value = lv + rv
                if value < best_value:
                    continue
                cost = tuple(lc[d] + rc[d] for d in range(dimensions))
                mask = fixed | lm | rm
                if (value == best_value
                        and (cost > best_cost or (cost == best_cost and not _lex_less(mask, best_mask)))):
                    continue
                if (ln & ~rm or lf & rm or rn & complement or rf & lm
                        or any(rc[d] > room[d] for d in range(dimensions))):
                    continue
                best_value, best_cost, best_mask = value, cost, mask

    return {"selected": [ids[i] for i in range(n) if best_mask & (1 << i)],
            "value": fixed_value + best_value,
            "cost": [fixed_cost[d] + best_cost[d] for d in range(dimensions)]}
