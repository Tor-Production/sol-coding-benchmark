"""An exact portfolio optimizer, using only the Python standard library."""

from bisect import bisect_left, bisect_right
from functools import cmp_to_key, lru_cache


def _validate(projects, budget, required):
    """Validate everything before performing any feasibility reductions."""
    def integer(x):
        return isinstance(x, int) and not isinstance(x, bool)

    if not isinstance(projects, list) or len(projects) > 32:
        raise ValueError("projects must be a list of at most 32 dictionaries")
    if (not isinstance(budget, list) or not 1 <= len(budget) <= 3
            or any(not integer(x) or x < 0 for x in budget)):
        raise ValueError("budget must contain one to three nonnegative integers")
    if not isinstance(required, (list, tuple)):
        raise ValueError("required must be a list or tuple")

    fields = {"id", "value", "cost", "requires", "excludes"}
    by_id = {}
    for p in projects:
        if not isinstance(p, dict) or set(p) != fields:
            raise ValueError("a project must have exactly the specified keys")
        name = p["id"]
        if not isinstance(name, str) or not name or name in by_id:
            raise ValueError("project IDs must be unique nonempty strings")
        if not integer(p["value"]):
            raise ValueError("project values must be integers, not bools")
        c = p["cost"]
        if (not isinstance(c, list) or len(c) != len(budget)
                or any(not integer(x) or x < 0 for x in c)):
            raise ValueError("project costs must match the budget dimensions")
        by_id[name] = p

    for name, p in by_id.items():
        for field in ("requires", "excludes"):
            refs = p[field]
            if not isinstance(refs, list):
                raise ValueError("requires and excludes must be lists")
            seen = set()
            for ref in refs:
                if (not isinstance(ref, str) or ref not in by_id
                        or ref == name or ref in seen):
                    raise ValueError("invalid or duplicate project reference")
                seen.add(ref)
    seen = set()
    for name in required:
        if not isinstance(name, str) or name not in by_id or name in seen:
            raise ValueError("required IDs must be distinct known IDs")
        seen.add(name)

    names = sorted(by_id)
    index = {name: i for i, name in enumerate(names)}
    values, costs, needs, conflicts = [], [], [], [0] * len(names)
    for i, name in enumerate(names):
        p = by_id[name]
        values.append(p["value"])
        costs.append(tuple(p["cost"]) + (0,) * (3 - len(budget)))
        needs.append(sum(1 << index[ref] for ref in p["requires"]))
        for ref in p["excludes"]:
            j = index[ref]
            conflicts[i] |= 1 << j
            conflicts[j] |= 1 << i

    closures, visiting = [0] * len(names), [False] * len(names)

    def close(i):
        if visiting[i]:
            raise ValueError("the dependency graph must be acyclic")
        if closures[i]:
            return closures[i]
        visiting[i] = True
        mask, deps = 1 << i, needs[i]
        while deps:
            bit = deps & -deps
            mask |= close(bit.bit_length() - 1)
            deps ^= bit
        visiting[i] = False
        closures[i] = mask
        return mask

    for i in range(len(names)):
        close(i)
    forced = 0
    for name in required:
        forced |= closures[index[name]]
    return names, values, costs, needs, conflicts, closures, forced


def _join_groups(left_groups, right_groups, limits):
    """Exact multidimensional multiple-choice knapsack.

    A group contains alternative nonempty selections; choosing none is also
    allowed. Each option has cost0, cost1, cost2, value, mask, ID preference.
    Callers ensure that equal numeric objectives cannot have a prefix ID
    tie, so maximizing ID preference bits implements the string tuple order.
    Each half uses sparse DP (equal costs retain the best value/ID choice).
    An offline orthant query joins the halves: a prefix minimum for one cost
    dimension, a Fenwick tree for two, and a Fenwick tree of Fenwick trees
    for three. Tree entries are ranks in the right half's objective order.
    """
    dimension = len(limits)
    cap = tuple(limits) + (0,) * (3 - dimension)

    def enumerate_half(groups):
        # Rows: cost0, cost1, cost2, value, selected mask, ID preference bits.
        states = {(0, 0, 0): (0, 0, 0, 0, 0, 0)}
        for options in groups:
            for row in list(states.values()):
                for a, b, c, value, mask, rank in options:
                    x, y, z = row[0] + a, row[1] + b, row[2] + c
                    if x > cap[0] or y > cap[1] or z > cap[2]:
                        continue
                    key, gain, preference = (x, y, z), row[3] + value, row[5] | rank
                    old = states.get(key)
                    if old is None or (gain, preference) > (old[3], old[5]):
                        states[key] = (x, y, z, gain, row[4] | mask, preference)
        return list(states.values())

    left = enumerate_half(left_groups)
    right = enumerate_half(right_groups)
    right.sort(key=lambda r: (-r[3], r[0], r[1], r[2], -r[5]))
    order = sorted(range(len(right)), key=lambda j: right[j][0])
    left.sort(key=lambda r: r[0], reverse=True)
    infinity = len(right)
    answer_key, answer = None, None

    def consider(row, j):
        nonlocal answer_key, answer
        if j == infinity:
            return
        other = right[j]
        x, y, z = (row[k] + other[k] for k in range(3))
        value, mask = row[3] + other[3], row[4] | other[4]
        key = (value, -x, -y, -z, row[5] | other[5])
        if answer_key is None or key > answer_key:
            answer_key, answer = key, (mask, value, (x, y, z))

    cursor = 0
    if dimension == 1:
        best = infinity
        for row in left:
            threshold = cap[0] - row[0]
            while cursor < len(order) and right[order[cursor]][0] <= threshold:
                best = min(best, order[cursor])
                cursor += 1
            consider(row, best)
        return answer

    coordinates = sorted({row[1] for row in right})
    count = len(coordinates)
    positions = [bisect_left(coordinates, row[1]) + 1 for row in right]
    if dimension == 2:
        tree = [infinity] * (count + 1)
        for row in left:
            threshold = cap[0] - row[0]
            while cursor < len(order) and right[order[cursor]][0] <= threshold:
                j = order[cursor]
                k = positions[j]
                while k <= count:
                    if j < tree[k]:
                        tree[k] = j
                    k += k & -k
                cursor += 1
            k = bisect_right(coordinates, cap[1] - row[1])
            best = infinity
            while k:
                if tree[k] < best:
                    best = tree[k]
                k -= k & -k
            consider(row, best)
        return answer

    inner = [[] for _ in range(count + 1)]
    for j, row in enumerate(right):
        k = positions[j]
        while k <= count:
            inner[k].append(row[2])
            k += k & -k
    for k in range(1, count + 1):
        inner[k] = sorted(set(inner[k]))
    trees = [[infinity] * (len(xs) + 1) for xs in inner]
    for row in left:
        threshold = cap[0] - row[0]
        while cursor < len(order) and right[order[cursor]][0] <= threshold:
            j = order[cursor]
            k, z = positions[j], right[j][2]
            while k <= count:
                t = bisect_left(inner[k], z) + 1
                tree = trees[k]
                while t < len(tree):
                    if j < tree[t]:
                        tree[t] = j
                    t += t & -t
                k += k & -k
            cursor += 1
        k = bisect_right(coordinates, cap[1] - row[1])
        z, best = cap[2] - row[2], infinity
        while k:
            t, tree = bisect_right(inner[k], z), trees[k]
            while t:
                if tree[t] < best:
                    best = tree[t]
                t -= t & -t
            k -= k & -k
        consider(row, best)
    return answer


def _independent(items, values, costs, limits, n):
    groups = [[(*costs[i], values[i], 1 << i, 1 << (n - 1 - i))] for i in items]
    middle = len(groups) // 2
    return _join_groups(groups[:middle], groups[middle:], limits)


def solve(projects, budget, required=()):
    """Return the globally optimal feasible portfolio, or None if infeasible.

    Branches include a project's entire dependency closure or exclude all
    its dependents. Integer fractional-knapsack and conflict-clique bounds
    prune only provably inferior branches. Independent residual problems
    are solved by meet in the middle rather than a full subset traversal.
    """
    names, values, costs, needs, conflicts, closures, forced = _validate(
        projects, budget, required)
    n, dimension = len(names), len(budget)
    cap = tuple(budget) + (0,) * (3 - dimension)
    all_mask = (1 << n) - 1

    @lru_cache(maxsize=32768)
    def totals(mask):
        value = x = y = z = 0
        while mask:
            bit = mask & -mask
            i = bit.bit_length() - 1
            value += values[i]
            a, b, c = costs[i]
            x, y, z = x + a, y + b, z + c
            mask ^= bit
        return value, (x, y, z)

    def ids(mask):
        return tuple(names[i] for i in range(n) if mask & (1 << i))

    dependents = [0] * n
    adjacent = [needs[i] | conflicts[i] for i in range(n)]
    for i, closure in enumerate(closures):
        mask = closure
        while mask:
            bit = mask & -mask
            dependents[bit.bit_length() - 1] |= 1 << i
            mask ^= bit
        mask = needs[i]
        while mask:
            bit = mask & -mask
            adjacent[bit.bit_length() - 1] |= 1 << i
            mask ^= bit

    # bans[i] contains every project whose closure conflicts with closure[i].
    bans, impossible = [0] * n, 0
    for i, closure in enumerate(closures):
        forbidden, mask = 0, closure
        while mask:
            bit = mask & -mask
            forbidden |= conflicts[bit.bit_length() - 1]
            mask ^= bit
        mask = forbidden
        while mask:
            bit = mask & -mask
            bans[i] |= dependents[bit.bit_length() - 1]
            mask ^= bit
        if forbidden & closure or any(a > b for a, b in zip(totals(closure)[1], cap)):
            impossible |= dependents[i]

    current_value, current_cost = totals(forced)
    if forced & impossible or any(a > b for a, b in zip(current_cost, cap)):
        return None
    forbidden, mask = impossible, forced
    while mask:
        bit = mask & -mask
        forbidden |= bans[bit.bit_length() - 1]
        mask ^= bit
    if forced & forbidden:
        return None
    available = all_mask & ~(forced | forbidden)
    best_value, best_cost, best_ids = current_value, current_cost, ids(forced)

    def improve(selected, value, cost):
        nonlocal best_value, best_cost, best_ids
        if value < best_value or (value == best_value and cost > best_cost):
            return
        chosen = ids(selected)
        if (value > best_value or cost < best_cost
                or chosen < best_ids):
            best_value, best_cost, best_ids = value, cost, chosen

    positive = sum(1 << i for i in range(n) if values[i] > 0)

    # Ratio comparisons use cross products; arbitrarily large integers never
    # pass through floating point, including in bounds and greedy incumbents.
    weights = [tuple(int(j == d) for j in range(3)) for d in range(dimension)]
    if dimension > 1:
        product = 1
        for b in budget:
            product *= b + 1
        weights.append(tuple(product // (budget[j] + 1) if j < dimension else 0
                             for j in range(3)))
    relaxations = []
    for weight in weights:
        expense = [sum(c * w for c, w in zip(row, weight)) for row in costs]

        def compare(i, j):
            cross = values[i] * expense[j] - values[j] * expense[i]
            return -1 if cross > 0 else 1 if cross < 0 else i - j

        order = sorted((i for i in range(n) if values[i] > 0), key=cmp_to_key(compare))
        relaxations.append((weight, expense, order))

    @lru_cache(maxsize=8192)
    def clique_bound(mask):
        """Cover positive vertices with disjoint conflict cliques."""
        remaining, upper = mask & positive, 0
        while remaining:
            vertices = [i for i in range(n) if remaining & (1 << i)]
            i = max(vertices, key=lambda j: ((bans[j] & remaining).bit_count(), values[j]))
            remaining &= ~(1 << i)
            maximum, candidates = values[i], remaining & bans[i]
            while candidates:
                vertices = [j for j in range(n) if candidates & (1 << j)]
                j = max(vertices, key=lambda k: ((bans[k] & candidates).bit_count(), values[k]))
                maximum = max(maximum, values[j])
                remaining &= ~(1 << j)
                candidates &= bans[j] & remaining
            upper += maximum
        return upper

    def lex_lower(selected, available, target):
        """Smallest relaxed ID tuple that can attain at least target value.

        Ignore costs, dependencies and conflicts, but keep fixed selections.
        Greedily extend the tuple only while ending it would miss a fixed ID
        or the target. Include the earliest optional ID if the remaining
        positive values can still reach the target after doing so.
        """
        potential = sum(values[i] for i in range(n) if available & positive & (1 << i))
        value, _ = totals(selected)
        rest, result = selected, []
        for i in range(n):
            if not rest and value >= target:
                break
            bit = 1 << i
            if rest & bit:
                result.append(names[i])
                rest ^= bit
            elif available & bit:
                potential -= max(values[i], 0)
                if value + values[i] + potential >= target:
                    result.append(names[i])
                    value += values[i]
        return tuple(result)

    def bounded(selected, available, value, cost):
        upper = value + clique_bound(available)
        if upper < best_value:
            return True
        for weight, expense, order in relaxations:
            room = sum((b - c) * w for b, c, w in zip(cap, cost, weight))
            gain = 0
            for i in order:
                if not available & (1 << i):
                    continue
                c = expense[i]
                if c <= room:
                    room -= c
                    gain += values[i]
                else:
                    gain += room * values[i] // c
                    break
            upper = min(upper, value + gain)
            if upper < best_value:
                return True
        if upper != best_value:
            return False

        # Lower cost bounds for reaching the incumbent's value, in each
        # dimension separately, with fractional projects allowed.
        lower = list(cost)
        for d, (_, expense, order) in enumerate(relaxations[:dimension]):
            need, minimum = best_value - value, 0
            if need <= 0:
                continue
            for i in order:
                if not available & (1 << i):
                    continue
                if values[i] < need:
                    need -= values[i]
                    minimum += expense[i]
                else:
                    minimum += (need * expense[i] + values[i] - 1) // values[i]
                    break
            lower[d] += minimum
        lower = tuple(lower)
        if lower > best_cost:
            return True
        return lower == best_cost and lex_lower(selected, available, best_value) >= best_ids

    def independent(selected, available, value, cost):
        items, free = [], 0
        mask = available
        while mask:
            bit = mask & -mask
            i = bit.bit_length() - 1
            if values[i] > 0:
                if costs[i] == (0, 0, 0):
                    selected |= bit
                    value += values[i]
                else:
                    items.append(i)
            elif values[i] == 0 and costs[i] == (0, 0, 0):
                free |= bit
            mask ^= bit
        total_value, total_cost = totals(sum(1 << i for i in items))
        if all(a + b <= c for a, b, c in zip(cost, total_cost, cap)):
            chosen, gain, spent = sum(1 << i for i in items), total_value, total_cost
        else:
            limits = [cap[d] - cost[d] for d in range(dimension)]
            chosen, gain, spent = _independent(items, values, costs, limits, n)
        selected |= chosen
        # Optional free zero-value IDs improve the tuple exactly when they
        # precede its last fixed member. Appending IDs would worsen a prefix.
        if selected:
            selected |= free & ((1 << (selected.bit_length() - 1)) - 1)
        improve(selected, value + gain, tuple(a + b for a, b in zip(cost, spent)))

    def greedy(mode):
        selected, remaining, value, cost = forced, available, current_value, current_cost
        while remaining:
            choice, best_gain, best_denominator = None, 0, 1
            mask = remaining
            while mask:
                bit = mask & -mask
                i = bit.bit_length() - 1
                gain, extra = totals(closures[i] & ~selected)
                mask ^= bit
                if gain <= 0 or any(a + b > c for a, b, c in zip(cost, extra, cap)):
                    continue
                denominator = 1 if mode == -1 else extra[mode] + 1
                if (choice is None or gain * best_denominator > best_gain * denominator):
                    choice, best_gain, best_denominator = i, gain, denominator
            if choice is None:
                break
            added = closures[choice] & ~selected
            gain, extra = totals(added)
            selected |= added
            remaining &= ~(added | bans[choice])
            value += gain
            cost = tuple(a + b for a, b in zip(cost, extra))
            improve(selected, value, cost)

    def components(selected, available, value, cost):
        """Solve small disconnected constraint components together exactly.

        Bound enumeration to 12 vertices per component and 2**16 choices
        per half. Larger connected graphs remain with branch-and-bound.
        Numeric ties use ID bits only when tied sets cannot be prefixes:
        either every optional value is positive or every optional cost is
        nonzero. The general search handles the other cases, including free
        zero-value dependency bundles with delicate prefix ordering.
        """
        if available.bit_count() < 14:
            return False
        members = [i for i in range(n) if available & (1 << i)]
        if (not all(values[i] > 0 for i in members)
                and not all(costs[i] != (0, 0, 0) for i in members)):
            return False
        remaining, parts = available, []
        while remaining:
            frontier, component = remaining & -remaining, 0
            while frontier:
                bit = frontier & -frontier
                frontier ^= bit
                component |= bit
                remaining &= ~bit
                frontier |= adjacent[bit.bit_length() - 1] & remaining
            if component.bit_count() > 12:
                return False
            parts.append(component)
        if len(parts) == 1:
            return False

        room = tuple(b - c for b, c in zip(cap, cost))
        groups = []
        for component in parts:
            members = [i for i in range(n) if component & (1 << i)]
            masks = [0]
            for i in members:
                masks += [mask | (1 << i) for mask in masks]
            options = []
            for mask in masks[1:]:
                if any((closures[i] & available & ~mask) or conflicts[i] & mask
                       for i in members if mask & (1 << i)):
                    continue
                gain, expense = totals(mask)
                # A nonpositive component choice is numerically dominated
                # by choosing none under the nonzero-cost/positive-value
                # condition above.
                if gain <= 0 or any(c > b for c, b in zip(expense, room)):
                    continue
                preference = sum(1 << (n - 1 - i) for i in members if mask & (1 << i))
                options.append((*expense, gain, mask, preference))
            if options:
                groups.append(options)

        halves, sizes = [[], []], [1, 1]
        for options in sorted(groups, key=len, reverse=True):
            side = 0 if sizes[0] <= sizes[1] else 1
            sizes[side] *= len(options) + 1
            if sizes[side] > 65536:
                return False
            halves[side].append(options)
        chosen, gain, expense = _join_groups(halves[0], halves[1], list(room[:dimension]))
        improve(selected | chosen, value + gain, tuple(a + b for a, b in zip(cost, expense)))
        return True

    for mode in range(-1, dimension):
        greedy(mode)

    def search(selected, available, value, cost):
        improve(selected, value, cost)
        # A closure that cannot fit cannot be used by any of its dependents.
        # Unneeded negative leaves (or costly zero leaves) can be removed:
        # dropping one strictly improves the numeric objective.
        while True:
            removed, mask = 0, available
            while mask:
                bit = mask & -mask
                i = bit.bit_length() - 1
                mask ^= bit
                if ((values[i] < 0 or (values[i] == 0 and costs[i] != (0, 0, 0)))
                        and dependents[i] & available == bit):
                    removed |= bit
                    continue
                _, extra = totals(closures[i] & ~selected)
                if any(a + b > c for a, b, c in zip(cost, extra, cap)):
                    removed |= dependents[i]
            removed &= available
            if not removed:
                break
            available &= ~removed
        if not available or bounded(selected, available, value, cost):
            return

        constrained, mask = [], available
        while mask:
            bit = mask & -mask
            i = bit.bit_length() - 1
            if (needs[i] | conflicts[i]) & available:
                constrained.append(i)
            mask ^= bit
        if not constrained:
            independent(selected, available, value, cost)
            return
        if components(selected, available, value, cost):
            return

        # Prefer a decision that propagates far in both branches. A shared
        # prerequisite is especially useful: deciding it exposes independence.
        def priority(i):
            yes = ((closures[i] | bans[i]) & available).bit_count()
            no = (dependents[i] & available).bit_count()
            return yes * no, yes + no, values[i], -i

        i = max(constrained, key=priority)
        added = closures[i] & ~selected
        gain, extra = totals(added)
        next_cost = tuple(a + b for a, b in zip(cost, extra))
        search(selected | added, available & ~(added | bans[i]), value + gain, next_cost)
        search(selected, available & ~dependents[i], value, cost)

    search(forced, available, current_value, current_cost)
    return {"selected": list(best_ids), "value": best_value, "cost": list(best_cost[:dimension])}
