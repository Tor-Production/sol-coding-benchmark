"""Exact portfolio optimization using bit sets and budget range queries."""

from bisect import bisect_left, bisect_right
from fractions import Fraction


def _lex_less(a, b):
    """Compare sorted ID tuples represented by masks in ascending ID order."""
    difference = a ^ b
    if not difference:
        return False
    first = difference & -difference
    # The first differing ID wins unless the other tuple ends here.
    if a & first:
        return b.bit_length() > first.bit_length()
    return a.bit_length() <= first.bit_length()


def _validate(projects, budget, required):
    def integer(x):
        return isinstance(x, int) and not isinstance(x, bool)

    if (not isinstance(budget, list) or not 1 <= len(budget) <= 3
            or any(not integer(x) or x < 0 for x in budget)):
        raise ValueError("budget must contain one to three nonnegative integers")
    if not isinstance(projects, list) or len(projects) > 32:
        raise ValueError("projects must be a list of at most 32 dictionaries")
    if not isinstance(required, (list, tuple)):
        raise ValueError("required must be a list or tuple")
    keys = {"id", "value", "cost", "requires", "excludes"}
    known = set()
    for p in projects:
        if not isinstance(p, dict) or set(p) != keys:
            raise ValueError("project dictionary has incorrect keys")
        name = p["id"]
        if not isinstance(name, str) or not name or name in known:
            raise ValueError("project IDs must be unique nonempty strings")
        known.add(name)
        if not integer(p["value"]):
            raise ValueError("project value must be an integer")
        cost = p["cost"]
        if (not isinstance(cost, list) or len(cost) != len(budget)
                or any(not integer(x) or x < 0 for x in cost)):
            raise ValueError("project cost has incorrect dimensions or values")
        for field in ("requires", "excludes"):
            refs = p[field]
            if not isinstance(refs, list):
                raise ValueError("project references must be lists")
            seen = set()
            for ref in refs:
                if (not isinstance(ref, str) or not ref or ref == name
                        or ref in seen):
                    raise ValueError("invalid or duplicate project reference")
                seen.add(ref)
    for p in projects:
        for field in ("requires", "excludes"):
            if any(ref not in known for ref in p[field]):
                raise ValueError("unknown project reference")
    seen = set()
    for ref in required:
        if not isinstance(ref, str) or ref not in known or ref in seen:
            raise ValueError("invalid or duplicate required ID")
        seen.add(ref)

    ordered = sorted(projects, key=lambda p: p["id"])
    index = {p["id"]: i for i, p in enumerate(ordered)}
    n = len(ordered)
    closure = [0] * n
    visiting = [False] * n

    def visit(i):
        if visiting[i]:
            raise ValueError("dependency graph must be acyclic")
        if closure[i]:
            return closure[i]
        visiting[i] = True
        mask = 1 << i
        for ref in ordered[i]["requires"]:
            mask |= visit(index[ref])
        visiting[i] = False
        closure[i] = mask
        return mask

    for i in range(n):
        visit(i)
    return ordered, index, closure


def _join(left, right, limits, base_mask, base_value, base_cost):
    """Optimize independent choices by orthogonal prefix maxima.

    Records are (cost tuple, value, mask). Resolve ID ties separately,
    retaining all records: tuple order is not preserved by set union.
    """
    dimensions = len(limits)

    def distinct(records):
        by_cost = {}
        for cost, value, _ in records:
            if cost not in by_cost or value > by_cost[cost]:
                by_cost[cost] = value
        return list(by_cost.items())

    queries = distinct(left)
    points = distinct(right)
    points.sort()
    scores = [(value,) + tuple(-x for x in cost) for cost, value in points]
    best_value = None
    best_cost = None

    def consider(cost, value, winner):
        nonlocal best_value, best_cost
        if winner < 0:
            return
        other_cost, other_value = points[winner]
        total_value = value + other_value
        total_cost = tuple(a + b for a, b in zip(cost, other_cost))
        if (best_value is None or total_value > best_value
                or (total_value == best_value and total_cost < best_cost)):
            best_value, best_cost = total_value, total_cost

    if dimensions == 1:
        coordinates = [cost[0] for cost, _ in points]
        prefix = []
        winner = -1
        for i in range(len(points)):
            if winner < 0 or scores[i] > scores[winner]:
                winner = i
            prefix.append(winner)
        for cost, value in queries:
            j = bisect_right(coordinates, limits[0] - cost[0]) - 1
            if j >= 0:
                consider(cost, value, prefix[j])
    else:
        # Sweep dimension zero; use a Fenwick prefix maximum for the
        # remaining axes (a sparse nested Fenwick tree for three dimensions).
        coordinates = sorted({cost[1] for cost, _ in points})
        size = len(coordinates)
        if dimensions == 2:
            tree = [-1] * (size + 1)

            def update(i):
                x = bisect_left(coordinates, points[i][0][1]) + 1
                score = scores[i]
                while x <= size:
                    previous = tree[x]
                    if previous < 0 or score > scores[previous]:
                        tree[x] = i
                    x += x & -x

            def query(remainder):
                x = bisect_right(coordinates, remainder[1])
                winner = -1
                while x:
                    candidate = tree[x]
                    if candidate >= 0 and (winner < 0 or scores[candidate] > scores[winner]):
                        winner = candidate
                    x -= x & -x
                return winner
        else:
            axes = [[] for _ in range(size + 1)]
            point_x = []
            for cost, _ in points:
                x = bisect_left(coordinates, cost[1]) + 1
                point_x.append(x)
                while x <= size:
                    axes[x].append(cost[2])
                    x += x & -x
            axes = [sorted(set(axis)) for axis in axes]
            tree = [[-1] * (len(axis) + 1) for axis in axes]

            def update(i):
                x = point_x[i]
                z = points[i][0][2]
                score = scores[i]
                while x <= size:
                    y = bisect_left(axes[x], z) + 1
                    row = tree[x]
                    while y < len(row):
                        previous = row[y]
                        if previous < 0 or score > scores[previous]:
                            row[y] = i
                        y += y & -y
                    x += x & -x

            def query(remainder):
                x = bisect_right(coordinates, remainder[1])
                z = remainder[2]
                winner = -1
                while x:
                    y = bisect_right(axes[x], z)
                    row = tree[x]
                    while y:
                        candidate = row[y]
                        if candidate >= 0 and (winner < 0 or scores[candidate] > scores[winner]):
                            winner = candidate
                        y -= y & -y
                    x -= x & -x
                return winner

        queries.sort(key=lambda record: limits[0] - record[0][0])
        position = 0
        for cost, value in queries:
            remainder = tuple(a - b for a, b in zip(limits, cost))
            while position < len(points) and points[position][0][0] <= remainder[0]:
                update(position)
                position += 1
            consider(cost, value, query(remainder))

    # The largest common ID suffices to resolve prefix-order differences
    # when a right choice is united with the fixed base and a left choice.
    groups = {}
    for cost, value, mask in right:
        groups.setdefault((cost, value), []).append(mask)
    cached = {}
    best_mask = None
    for cost, value, mask in left:
        complement = tuple(a - b for a, b in zip(best_cost, cost))
        key = (complement, best_value - value)
        candidates = groups.get(key)
        if candidates is None:
            continue
        common = mask | base_mask
        last = common.bit_length()
        cache_key = (key, last)
        if cache_key not in cached:
            sentinel = 1 << (last - 1) if last else 0
            winner = candidates[0]
            for candidate in candidates[1:]:
                if _lex_less(candidate | sentinel, winner | sentinel):
                    winner = candidate
            cached[cache_key] = winner
        combined = common | cached[cache_key]
        if best_mask is None or _lex_less(combined, best_mask):
            best_mask = combined
    return (best_mask, base_value + best_value,
            tuple(a + b for a, b in zip(base_cost, best_cost)))


def solve(projects, budget, required=()):
    """Return the globally optimal feasible portfolio, or None if impossible."""
    ordered, index, closure = _validate(projects, budget, required)
    n = len(ordered)
    dimensions = len(budget)
    limits = tuple(budget)
    zero = (0,) * dimensions
    values = [p["value"] for p in ordered]
    costs = [tuple(p["cost"]) for p in ordered]
    all_mask = (1 << n) - 1
    reverse = [0] * n
    exclusions = [0] * n
    for i, p in enumerate(ordered):
        for ref in p["excludes"]:
            j = index[ref]
            exclusions[i] |= 1 << j
            exclusions[j] |= 1 << i
        mask = closure[i]
        while mask:
            bit = mask & -mask
            reverse[bit.bit_length() - 1] |= 1 << i
            mask ^= bit

    def totals(mask):
        value = 0
        cost = [0] * dimensions
        while mask:
            bit = mask & -mask
            i = bit.bit_length() - 1
            value += values[i]
            for d in range(dimensions):
                cost[d] += costs[i][d]
            mask ^= bit
        return value, tuple(cost)

    def union_masks(mask, table):
        result = 0
        while mask:
            bit = mask & -mask
            result |= table[bit.bit_length() - 1]
            mask ^= bit
        return result

    bans = [union_masks(union_masks(c, exclusions), reverse) for c in closure]
    impossible = 0
    for i in range(n):
        _, cost = totals(closure[i])
        if closure[i] & bans[i] or any(a > b for a, b in zip(cost, limits)):
            impossible |= reverse[i]
    base = 0
    for ref in required:
        base |= closure[index[ref]]
    base_value, base_cost = totals(base)
    if (base & impossible or base & union_masks(base, exclusions)
            or any(a > b for a, b in zip(base_cost, limits))):
        return None
    available = all_mask & ~(base | impossible | union_masks(base, bans))
    best_mask, best_value, best_cost = base, base_value, base_cost

    def improve(mask, value, cost):
        nonlocal best_mask, best_value, best_cost
        if (value > best_value or (value == best_value and
                (cost < best_cost or (cost == best_cost and _lex_less(mask, best_mask))))):
            best_mask, best_value, best_cost = mask, value, cost

    def components(remaining):
        result = []
        unseen = remaining
        while unseen:
            frontier = unseen & -unseen
            group = 0
            while frontier:
                group |= frontier
                unseen &= ~frontier
                neighbors = union_masks(frontier, closure)
                neighbors |= union_masks(frontier, reverse)
                neighbors |= union_masks(frontier, exclusions)
                frontier = neighbors & unseen
            result.append(group)
        return result

    def pivot(remaining):
        indices = [i for i in range(n) if remaining >> i & 1]
        return max(indices, key=lambda i: (
            ((closure[i] | reverse[i] | bans[i]) & remaining).bit_count(),
            values[i], -i))

    def choices(group, remainder):
        records = []

        def enumerate_choices(remaining, mask, value, cost):
            if not remaining:
                records.append((cost, value, mask))
                return
            i = pivot(remaining)
            addition = closure[i] & remaining
            extra_value, extra_cost = totals(addition)
            new_cost = tuple(a + b for a, b in zip(cost, extra_cost))
            if not (bans[i] & mask) and all(a <= b for a, b in zip(new_cost, remainder)):
                enumerate_choices(remaining & ~(addition | bans[i]),
                                  mask | addition, value + extra_value, new_cost)
            enumerate_choices(remaining & ~reverse[i], mask, value, cost)

        enumerate_choices(group, 0, 0, zero)
        return records

    def split_optimize(remaining, selected, value, cost):
        groups = components(remaining)
        # Bound enumeration before doing it; larger connected components
        # are handled by propagation/search, often exposing smaller groups.
        if any(group.bit_count() > 16 for group in groups):
            return False
        sides = [[], []]
        counts = [0, 0]
        for group in sorted(groups, key=int.bit_count, reverse=True):
            side = 0 if counts[0] <= counts[1] else 1
            sides[side].append(group)
            counts[side] += group.bit_count()
        if max(counts) > 17:
            return False
        remainder = tuple(a - b for a, b in zip(limits, cost))
        lists = []
        for side in sides:
            records = [(zero, 0, 0)]
            for group in side:
                options = choices(group, remainder)
                combined = []
                for first_cost, first_value, first_mask in records:
                    for other_cost, other_value, other_mask in options:
                        new_cost = tuple(a + b for a, b in zip(first_cost, other_cost))
                        if all(a <= b for a, b in zip(new_cost, remainder)):
                            combined.append((new_cost, first_value + other_value,
                                             first_mask | other_mask))
                records = combined
            lists.append(records)
        improve(*_join(lists[0], lists[1], remainder, selected, value, cost))
        return True

    # Fractional knapsack relaxations ignore dependencies/conflicts and
    # negative values. Integer division gives conservative exact bounds.
    positive = [i for i in range(n) if values[i] > 0]
    ratio_orders = []
    for d in range(dimensions):
        free = [i for i in positive if costs[i][d] == 0]
        charged = sorted((i for i in positive if costs[i][d]),
                         key=lambda i: Fraction(values[i], costs[i][d]), reverse=True)
        ratio_orders.append((free, charged))

    # A disjoint clique cover supplies an additional conflict upper bound.
    cliques = []
    for i in sorted(positive, key=lambda i: exclusions[i].bit_count(), reverse=True):
        for k, clique in enumerate(cliques):
            if clique & exclusions[i] == clique:
                cliques[k] |= 1 << i
                break
        else:
            cliques.append(1 << i)

    def upper_bound(remaining, value, cost):
        upper = value
        for clique in cliques:
            members = clique & remaining
            maximum = 0
            while members:
                bit = members & -members
                maximum = max(maximum, values[bit.bit_length() - 1])
                members ^= bit
            upper += maximum
        for d, (free, charged) in enumerate(ratio_orders):
            bound = value + sum(values[i] for i in free if remaining >> i & 1)
            capacity = limits[d] - cost[d]
            for i in charged:
                if not (remaining >> i & 1):
                    continue
                if costs[i][d] <= capacity:
                    capacity -= costs[i][d]
                    bound += values[i]
                else:
                    bound += capacity * values[i] // costs[i][d]
                    break
            upper = min(upper, bound)
        return upper

    def search(remaining, selected, value, cost):
        improve(selected, value, cost)
        if not remaining:
            return
        bound = upper_bound(remaining, value, cost)
        if bound < best_value or (bound == best_value and cost > best_cost):
            return
        if bound == best_value and cost == best_cost:
            # Least unconstrained superset: include all available IDs
            # preceding the last mandatory ID, and no IDs after it.
            before_last = (1 << selected.bit_length()) - 1
            optimistic = selected | (remaining & before_last)
            if not _lex_less(optimistic, best_mask):
                return
        if split_optimize(remaining, selected, value, cost):
            return
        i = pivot(remaining)
        addition = closure[i] & remaining
        extra_value, extra_cost = totals(addition)
        new_cost = tuple(a + b for a, b in zip(cost, extra_cost))

        def take():
            if not (bans[i] & selected) and all(a <= b for a, b in zip(new_cost, limits)):
                search(remaining & ~(addition | bans[i]), selected | addition,
                       value + extra_value, new_cost)

        if extra_value >= 0:
            take()
            search(remaining & ~reverse[i], selected, value, cost)
        else:
            search(remaining & ~reverse[i], selected, value, cost)
            take()

    search(available, base, base_value, base_cost)
    return {"selected": [p["id"] for i, p in enumerate(ordered) if best_mask >> i & 1],
            "value": best_value, "cost": list(best_cost)}
