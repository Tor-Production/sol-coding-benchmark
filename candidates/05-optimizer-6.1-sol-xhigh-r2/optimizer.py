"""Exact portfolio optimization, with integer bounds and bit-set constraints."""

from bisect import bisect_left, bisect_right
from functools import cmp_to_key


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

    fields = {"id", "value", "cost", "requires", "excludes"}
    by_id = {}
    for project in projects:
        if not isinstance(project, dict) or set(project) != fields:
            raise ValueError("each project must have exactly the specified keys")
        name = project["id"]
        if not isinstance(name, str) or not name or name in by_id:
            raise ValueError("project IDs must be unique nonempty strings")
        if not _integer(project["value"]):
            raise ValueError("project values must be integers, excluding bool")
        cost = project["cost"]
        if (not isinstance(cost, list) or len(cost) != len(budget)
                or any(not _integer(x) or x < 0 for x in cost)):
            raise ValueError("project costs must match the budget dimensions")
        for field in ("requires", "excludes"):
            refs = project[field]
            if not isinstance(refs, list):
                raise ValueError("project references must be lists")
            seen = set()
            for ref in refs:
                if (not isinstance(ref, str) or ref == name or ref in seen):
                    raise ValueError("invalid or duplicate project reference")
                seen.add(ref)
        by_id[name] = project

    names = sorted(by_id)
    index = {name: i for i, name in enumerate(names)}
    values, costs, needs, conflicts = [], [], [], [0] * len(names)
    for i, name in enumerate(names):
        project = by_id[name]
        values.append(project["value"])
        costs.append(tuple(project["cost"]))
        need = 0
        for field in ("requires", "excludes"):
            for ref in project[field]:
                if ref not in index:
                    raise ValueError("unknown project reference")
                j = index[ref]
                if field == "requires":
                    need |= 1 << j
                else:
                    conflicts[i] |= 1 << j
                    conflicts[j] |= 1 << i
        needs.append(need)

    required_mask = 0
    for name in required:
        if not isinstance(name, str) or name not in index:
            raise ValueError("unknown required project")
        bit = 1 << index[name]
        if required_mask & bit:
            raise ValueError("duplicate required project")
        required_mask |= bit

    # Validate the whole graph, including projects that will never be affordable.
    closures = [0] * len(names)
    visiting = set()

    def closure(i):
        if i in visiting:
            raise ValueError("dependency cycle")
        if closures[i]:
            return closures[i]
        visiting.add(i)
        result = 1 << i
        mask = needs[i]
        while mask:
            bit = mask & -mask
            mask -= bit
            result |= closure(bit.bit_length() - 1)
        visiting.remove(i)
        closures[i] = result
        return result

    for i in range(len(names)):
        closure(i)
    return names, values, costs, closures, conflicts, required_mask


def _independent(items, values, costs, capacity, dimensions, n):
    """Optimize positive independent items by offline dominance queries.

    There are at most 2**16 subsets on either side. Sweep one resource and
    query a Fenwick tree on the other one or two resources. The stored key
    orders value, all original costs, and IDs, in precisely that order.
    """
    zero = (0,) * dimensions
    total_cost = tuple(sum(costs[i][d] for i in items) for d in range(dimensions))
    if all(total_cost[d] <= capacity[d] for d in range(dimensions)):
        return sum(1 << i for i in items), sum(values[i] for i in items), total_cost

    def enumerate_half(half, keyed):
        records = [(zero, (0,) * (dimensions + 2), 0)] if keyed else [(zero, 0, 0)]
        for i in half:
            additions = []
            ci, vi, bit = costs[i], values[i], 1 << i
            lex_bit = 1 << (n - 1 - i)
            for cost, value, mask in records:
                new_cost = tuple(cost[d] + ci[d] for d in range(dimensions))
                if any(new_cost[d] > capacity[d] for d in range(dimensions)):
                    continue
                if keyed:
                    key = (value[0] + vi,) + tuple(-c for c in new_cost) + (value[-1] + lex_bit,)
                    additions.append((new_cost, key, mask | bit))
                else:
                    additions.append((new_cost, value + vi, mask | bit))
            records.extend(additions)
        return records

    split = len(items) // 2
    left = enumerate_half(items[:split], False)
    right = enumerate_half(items[split:], True)
    # Constraints whose capacity covers every item need no range query.
    axes = [d for d in range(dimensions) if total_cost[d] > capacity[d]]
    best_key, best_mask, best_cost = None, 0, zero

    def consider(a, b):
        nonlocal best_key, best_mask, best_cost
        total = tuple(a[0][d] + b[0][d] for d in range(dimensions))
        lex = b[1][-1]
        mask = a[2]
        while mask:
            bit = mask & -mask
            mask -= bit
            lex |= 1 << (n - bit.bit_length())
        key = (a[1] + b[1][0],) + tuple(-c for c in total) + (lex,)
        if best_key is None or key > best_key:
            best_key, best_mask, best_cost = key, a[2] | b[2], total

    sweep = axes[0]
    right.sort(key=lambda r: r[0][sweep])
    left.sort(key=lambda r: -r[0][sweep])
    pos = 0
    if len(axes) == 1:
        best = right[0]
        for a in left:
            limit = capacity[sweep] - a[0][sweep]
            while pos < len(right) and right[pos][0][sweep] <= limit:
                b = right[pos]
                if b[1] > best[1]:
                    best = b
                pos += 1
            consider(a, best)
    elif len(axes) == 2:
        y = axes[1]
        coords = sorted({r[0][y] for r in right})
        tree = [-1] * (len(coords) + 1)
        keys = [r[1] for r in right]
        for a in left:
            limit = capacity[sweep] - a[0][sweep]
            while pos < len(right) and right[pos][0][sweep] <= limit:
                j = bisect_left(coords, right[pos][0][y]) + 1
                while j < len(tree):
                    old = tree[j]
                    if old < 0 or keys[pos] > keys[old]:
                        tree[j] = pos
                    j += j & -j
                pos += 1
            j = bisect_right(coords, capacity[y] - a[0][y])
            best = -1
            while j:
                old = tree[j]
                if old >= 0 and (best < 0 or keys[old] > keys[best]):
                    best = old
                j -= j & -j
            if best >= 0:
                consider(a, right[best])
    else:
        y, z = axes[1:]
        coords = sorted({r[0][y] for r in right})
        # A compressed Fenwick tree of Fenwick trees: only coordinates of
        # actual right-hand points are allocated, rather than a dense grid.
        zs = [[] for _ in range(len(coords) + 1)]
        positions = []
        for b in right:
            j = bisect_left(coords, b[0][y]) + 1
            positions.append(j)
            while j < len(zs):
                zs[j].append(b[0][z])
                j += j & -j
        zs = [sorted(set(row)) for row in zs]
        trees = [[-1] * (len(row) + 1) for row in zs]
        keys = [r[1] for r in right]
        for a in left:
            limit = capacity[sweep] - a[0][sweep]
            while pos < len(right) and right[pos][0][sweep] <= limit:
                j, cz = positions[pos], right[pos][0][z]
                while j < len(zs):
                    k = bisect_left(zs[j], cz) + 1
                    row = trees[j]
                    while k < len(row):
                        old = row[k]
                        if old < 0 or keys[pos] > keys[old]:
                            row[k] = pos
                        k += k & -k
                    j += j & -j
                pos += 1
            j = bisect_right(coords, capacity[y] - a[0][y])
            cz, best = capacity[z] - a[0][z], -1
            while j:
                k = bisect_right(zs[j], cz)
                row = trees[j]
                while k:
                    old = row[k]
                    if old >= 0 and (best < 0 or keys[old] > keys[best]):
                        best = old
                    k -= k & -k
                j -= j & -j
            if best >= 0:
                consider(a, right[best])
    return best_mask, best_key[0], best_cost


def solve(projects, budget, required=()):
    """Return the globally optimal feasible portfolio, or None if infeasible."""
    names, values, costs, closures, conflicts, required_mask = _validate(projects, budget, required)
    n, dimensions = len(names), len(budget)
    budget = tuple(budget)
    all_mask = (1 << n) - 1
    dependents = [0] * n
    for i, closure in enumerate(closures):
        mask = closure
        while mask:
            bit = mask & -mask
            mask -= bit
            dependents[bit.bit_length() - 1] |= 1 << i

    def totals(mask):
        value, cost = 0, [0] * dimensions
        while mask:
            bit = mask & -mask
            mask -= bit
            i = bit.bit_length() - 1
            value += values[i]
            for d in range(dimensions):
                cost[d] += costs[i][d]
        return value, tuple(cost)

    # Including i includes its closure, and excludes every dependent of a
    # conflicting project. Excluding i excludes all its dependents as well.
    blocked = [0] * n
    impossible = 0
    closure_totals = []
    for i in range(n):
        value, cost = totals(closures[i])
        closure_totals.append((value, cost))
        mask, excluded = closures[i], 0
        while mask:
            bit = mask & -mask
            mask -= bit
            excluded |= conflicts[bit.bit_length() - 1]
        mask = excluded
        while mask:
            bit = mask & -mask
            mask -= bit
            blocked[i] |= dependents[bit.bit_length() - 1]
        if (closures[i] & excluded
                or any(cost[d] > budget[d] for d in range(dimensions))):
            impossible |= dependents[i]

    selected, forbidden = 0, impossible
    mask = required_mask
    while mask:
        bit = mask & -mask
        mask -= bit
        i = bit.bit_length() - 1
        selected |= closures[i]
        forbidden |= blocked[i]
    if selected & forbidden:
        return None
    initial_value, initial_cost = totals(selected)
    if any(initial_cost[d] > budget[d] for d in range(dimensions)):
        return None
    available = all_mask & ~(selected | forbidden)

    def ids(mask):
        return tuple(names[i] for i in range(n) if mask & (1 << i))

    best_value, best_cost = initial_value, initial_cost
    best_ids = ids(selected)

    def consider(mask, value, cost):
        nonlocal best_value, best_cost, best_ids
        if value < best_value or (value == best_value and cost > best_cost):
            return
        candidate_ids = ids(mask)
        if (value > best_value or cost < best_cost or candidate_ids < best_ids):
            best_value, best_cost, best_ids = value, cost, candidate_ids

    positive = sum(1 << i for i in range(n) if values[i] > 0)

    def density_order(d):
        def compare(i, j):
            ci, cj = costs[i][d], costs[j][d]
            if not ci or not cj:
                if bool(ci) != bool(cj):
                    return 1 if ci else -1
            difference = values[i] * cj - values[j] * ci
            return -1 if difference > 0 else 1 if difference < 0 else i - j
        return sorted((i for i in range(n) if values[i] > 0), key=cmp_to_key(compare))

    orders = [density_order(d) for d in range(dimensions)]
    # A fixed clique partition provides another admissible upper bound: a
    # feasible set can take at most one positive-valued member of each clique.
    clique_orders = [list(range(n)), sorted(range(n), key=lambda i: -conflicts[i].bit_count())]
    covers = []
    for order in clique_orders:
        cover = []
        for i in order:
            if values[i] <= 0:
                continue
            for k, clique in enumerate(cover):
                if clique & conflicts[i] == clique:
                    cover[k] |= 1 << i
                    break
            else:
                cover.append(1 << i)
        covers.append(cover)
    cliques = min(covers, key=lambda cover: sum(max(values[i] for i in range(n)
                                                  if clique & (1 << i)) for clique in cover))
    clique_members = [sorted((i for i in range(n) if clique & (1 << i)),
                             key=lambda i: -values[i]) for clique in cliques]

    # Branch on promising complete dependency bundles. All comparisons use
    # integers, including density comparisons for arbitrarily large inputs.
    scales = [max(1, b) for b in budget]
    scale_product = 1
    for scale in scales:
        scale_product *= scale
    bundle_cost = [sum(cost[d] * (scale_product // scales[d]) for d in range(dimensions))
                   for _, cost in closure_totals]

    def branch_compare(i, j):
        vi, vj = closure_totals[i][0], closure_totals[j][0]
        if (vi > 0) != (vj > 0):
            return -1 if vi > 0 else 1
        ci, cj = bundle_cost[i], bundle_cost[j]
        if vi > 0 and vj > 0:
            if bool(ci) != bool(cj):
                return 1 if ci else -1
            difference = vi * cj - vj * ci
            if difference:
                return -1 if difference > 0 else 1
        return i - j

    branch_order = sorted(range(n), key=cmp_to_key(branch_compare))

    # Seed a feasible incumbent to make the bounds useful from the first node.
    greedy_chosen, greedy_candidates = selected, available
    greedy_value, greedy_cost = initial_value, initial_cost
    for i in branch_order:
        if not greedy_candidates & (1 << i):
            continue
        extra = closures[i] & ~greedy_chosen
        gain, spent = totals(extra)
        new_cost = tuple(greedy_cost[d] + spent[d] for d in range(dimensions))
        if (gain > 0 and not extra & ~greedy_candidates
                and not blocked[i] & greedy_chosen
                and all(new_cost[d] <= budget[d] for d in range(dimensions))):
            greedy_chosen |= extra
            greedy_candidates &= ~(extra | blocked[i])
            greedy_value += gain
            greedy_cost = new_cost
    consider(greedy_chosen, greedy_value, greedy_cost)

    def independent_tail(chosen, candidates, value, cost):
        items, free = [], 0
        mask = candidates
        while mask:
            bit = mask & -mask
            mask -= bit
            i = bit.bit_length() - 1
            if values[i] > 0:
                if not any(costs[i]):
                    chosen |= bit
                    value += values[i]
                else:
                    items.append(i)
            elif values[i] == 0 and not any(costs[i]):
                free |= bit
        capacity = tuple(budget[d] - cost[d] for d in range(dimensions))
        if items:
            extra, gain, spent = _independent(items, values, costs, capacity, dimensions, n)
            chosen |= extra
            value += gain
            cost = tuple(cost[d] + spent[d] for d in range(dimensions))
        # A free zero-value item improves a sorted ID tuple exactly when it
        # precedes its last selected ID. Appending an ID worsens a prefix tie.
        if chosen:
            chosen |= free & ((1 << (chosen.bit_length() - 1)) - 1)
        consider(chosen, value, cost)

    def search(chosen, candidates, value, cost):
        consider(chosen, value, cost)
        if not candidates:
            return

        # Optional nonpositive leaves with a strictly worse value or cost
        # cannot improve a solution. Removing them can expose more leaves.
        while True:
            removed = 0
            mask = candidates & ~positive
            while mask:
                bit = mask & -mask
                mask -= bit
                i = bit.bit_length() - 1
                if (dependents[i] & candidates == bit
                        and (values[i] < 0 or any(costs[i]))):
                    removed |= bit
            if not removed:
                break
            candidates &= ~removed
        if not candidates:
            return

        upper = value
        for members in clique_members:
            for i in members:
                if candidates & (1 << i):
                    upper += values[i]
                    break
        if upper < best_value:
            return
        # Fractional knapsack in each resource is a relaxation. Integer floor
        # of its objective is still an upper bound on the integer optimum.
        for d in range(dimensions):
            capacity, bound = budget[d] - cost[d], value
            for i in orders[d]:
                if not candidates & (1 << i):
                    continue
                ci = costs[i][d]
                if ci <= capacity:
                    capacity -= ci
                    bound += values[i]
                else:
                    bound += capacity * values[i] // ci
                    break
            upper = min(upper, bound)
            if upper < best_value:
                return
        if upper == best_value:
            # Minimum resource needed to reach the incumbent's value, again
            # relaxed to fractional positive items and rounded upwards.
            target, lower = best_value - value, list(cost)
            if target > 0:
                for d in range(dimensions):
                    remaining, spent = target, 0
                    for i in orders[d]:
                        if not candidates & (1 << i):
                            continue
                        vi, ci = values[i], costs[i][d]
                        if vi < remaining:
                            remaining -= vi
                            spent += ci
                        else:
                            spent += (remaining * ci + vi - 1) // vi
                            break
                    lower[d] += spent
            lower = tuple(lower)
            if lower > best_cost:
                return
            if lower == best_cost:
                # The lexicographically smallest *possible* superset ignores
                # feasibility and includes every available ID before the
                # last mandatory selected ID. It is a safe lower bound.
                lex_mask = chosen
                if chosen:
                    lex_mask |= candidates & ((1 << (chosen.bit_length() - 1)) - 1)
                if ids(lex_mask) >= best_ids:
                    return

        # Large unconstrained remainders use meet-in-the-middle range queries
        # instead of visiting every subset. Check bounds before paying for it.
        if candidates.bit_count() >= 18:
            mask, independent = candidates, True
            while mask:
                bit = mask & -mask
                mask -= bit
                i = bit.bit_length() - 1
                if (closures[i] & candidates != bit or conflicts[i] & candidates):
                    independent = False
                    break
            if independent:
                independent_tail(chosen, candidates, value, cost)
                return

        i = next(i for i in branch_order if candidates & (1 << i))
        extra = closures[i] & ~chosen
        gain, spent = totals(extra)
        new_cost = tuple(cost[d] + spent[d] for d in range(dimensions))
        if (not extra & ~candidates
                and not blocked[i] & chosen
                and all(new_cost[d] <= budget[d] for d in range(dimensions))):
            search(chosen | extra, candidates & ~(extra | blocked[i]), value + gain, new_cost)
        search(chosen, candidates & ~dependents[i], value, cost)

    search(selected, available, initial_value, initial_cost)
    return {"selected": list(best_ids), "value": best_value, "cost": list(best_cost)}
