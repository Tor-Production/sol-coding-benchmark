"""Exact constrained portfolio optimization.

The public entry point is :func:`solve`.  The implementation deliberately
uses integer bit masks: the contract limits an instance to 32 projects, so
closure/conflict propagation is both simple and cheap in that representation.
"""

from bisect import bisect_left, bisect_right
from functools import cmp_to_key


_KEYS = {"id", "value", "cost", "requires", "excludes"}


def _validate(projects, budget, required):
    """Validate and copy the input into immutable, ID-sorted records."""
    if type(projects) is not list or len(projects) > 32:
        raise ValueError("projects must be a list containing at most 32 items")
    if type(budget) is not list or not 1 <= len(budget) <= 3:
        raise ValueError("budget must be a list with one to three dimensions")
    if any(type(x) is not int or x < 0 for x in budget):
        raise ValueError("budget entries must be nonnegative integers")
    if type(required) not in (list, tuple):
        raise ValueError("required must be a list or tuple")

    copied = []
    ids = set()
    for project in projects:
        if type(project) is not dict or set(project) != _KEYS:
            raise ValueError("each project must be a dictionary with exactly the required keys")

        project_id = project["id"]
        value = project["value"]
        cost = project["cost"]
        requires = project["requires"]
        excludes = project["excludes"]

        if type(project_id) is not str or not project_id or project_id in ids:
            raise ValueError("project IDs must be unique nonempty strings")
        ids.add(project_id)
        if type(value) is not int:
            raise ValueError("project values must be integers (not bool)")
        if type(cost) is not list or len(cost) != len(budget):
            raise ValueError("project costs must match the budget dimensions")
        if any(type(x) is not int or x < 0 for x in cost):
            raise ValueError("project costs must be nonnegative integers")
        if type(requires) is not list or type(excludes) is not list:
            raise ValueError("requires and excludes must be lists")
        if any(type(x) is not str for x in requires + excludes):
            raise ValueError("project references must be strings")
        if len(set(requires)) != len(requires) or len(set(excludes)) != len(excludes):
            raise ValueError("project reference lists must not contain duplicates")

        # Copy every nested container so no later operation can touch caller data.
        copied.append((project_id, value, tuple(cost), tuple(requires), tuple(excludes)))

    by_id = {record[0]: record for record in copied}
    for project_id, _value, _cost, requires, excludes in copied:
        for reference in requires + excludes:
            if reference == project_id or reference not in by_id:
                raise ValueError("self-references and unknown project IDs are invalid")

    if any(type(x) is not str for x in required):
        raise ValueError("required entries must be project IDs")
    if len(set(required)) != len(required) or any(x not in by_id for x in required):
        raise ValueError("required IDs must be distinct and known")

    records = sorted(copied, key=lambda record: record[0])
    index = {record[0]: i for i, record in enumerate(records)}
    direct_requires = [tuple(index[x] for x in record[3]) for record in records]

    # A DFS here both validates acyclicity and computes transitive closures.
    n = len(records)
    state = [0] * n
    closures = [0] * n

    def visit(i):
        if state[i] == 1:
            raise ValueError("the dependency graph must be acyclic")
        if state[i] == 2:
            return closures[i]
        state[i] = 1
        mask = 1 << i
        for dependency in direct_requires[i]:
            mask |= visit(dependency)
        state[i] = 2
        closures[i] = mask
        return mask

    for i in range(n):
        visit(i)

    required_mask = 0
    for project_id in required:
        required_mask |= 1 << index[project_id]

    return records, tuple(budget), direct_requires, closures, required_mask


def _enumerate_sums(items, dimensions, caps):
    """Enumerate feasible (cost tuple, value) pairs for at most 16 items."""
    zero = (0,) * dimensions
    states = [(zero, 0)]
    for _index, value, cost, _mask in items:
        additions = []
        for old_cost, old_value in states:
            new_cost = tuple(old_cost[d] + cost[d] for d in range(dimensions))
            if all(new_cost[d] <= caps[d] for d in range(dimensions)):
                additions.append((new_cost, old_value + value))
        states.extend(additions)
    return states


def _best_independent_primary(items, capacities, dimensions):
    """Return the best (value, cost tuple) for independent 0/1 items.

    This is meet-in-the-middle.  One half supplies dominance points and the
    other half supplies upper-bound queries.  The 3-D case uses a compressed
    Fenwick tree of Fenwick trees, keeping the worst case around
    O(2^(m/2) log^2(2^(m/2))) rather than enumerating 2^m portfolios.
    """
    midpoint = len(items) // 2
    left = _enumerate_sums(items[:midpoint], dimensions, capacities)
    right_raw = _enumerate_sums(items[midpoint:], dimensions, capacities)

    # At an identical cost only the greatest value can matter.
    right_by_cost = {}
    for cost, value in right_raw:
        if value > right_by_cost.get(cost, value - 1):
            right_by_cost[cost] = value
    right = [(cost, value) for cost, value in right_by_cost.items()]

    best = None  # tuple ordered as (value, -cost[0], ...)

    if dimensions == 1:
        right.sort(key=lambda entry: entry[0][0])
        right_costs = []
        prefix = []
        current = None
        for cost, value in right:
            key = (value, -cost[0])
            if current is None or key > current:
                current = key
            right_costs.append(cost[0])
            prefix.append(current)

        for left_cost, left_value in left:
            position = bisect_right(right_costs, capacities[0] - left_cost[0]) - 1
            if position >= 0:
                candidate = prefix[position]
                total = (left_value + candidate[0], candidate[1] - left_cost[0])
                if best is None or total > best:
                    best = total

    elif dimensions == 2:
        right.sort(key=lambda entry: entry[0][0])
        second_coords = sorted({cost[1] for cost, _value in right})
        tree = [None] * (len(second_coords) + 1)

        def update(second, key):
            position = bisect_left(second_coords, second) + 1
            while position < len(tree):
                old = tree[position]
                if old is None or key > old:
                    tree[position] = key
                position += position & -position

        def query(second):
            position = bisect_right(second_coords, second)
            result = None
            while position:
                value = tree[position]
                if value is not None and (result is None or value > result):
                    result = value
                position -= position & -position
            return result

        queries = sorted(left, key=lambda entry: capacities[0] - entry[0][0])
        point = 0
        for left_cost, left_value in queries:
            cap0 = capacities[0] - left_cost[0]
            while point < len(right) and right[point][0][0] <= cap0:
                cost, value = right[point]
                update(cost[1], (value, -cost[0], -cost[1]))
                point += 1
            candidate = query(capacities[1] - left_cost[1])
            if candidate is not None:
                total = (
                    left_value + candidate[0],
                    candidate[1] - left_cost[0],
                    candidate[2] - left_cost[1],
                )
                if best is None or total > best:
                    best = total

    else:
        right.sort(key=lambda entry: entry[0][0])
        second_coords = sorted({cost[1] for cost, _value in right})
        outer_size = len(second_coords)

        # Pre-compress exactly the third-coordinate values which can be
        # updated in each outer Fenwick node.
        inner_coords = [[] for _ in range(outer_size + 1)]
        for cost, _value in right:
            position = bisect_left(second_coords, cost[1]) + 1
            while position <= outer_size:
                inner_coords[position].append(cost[2])
                position += position & -position
        for position in range(1, outer_size + 1):
            if inner_coords[position]:
                inner_coords[position] = sorted(set(inner_coords[position]))
        trees = [[None] * (len(coords) + 1) for coords in inner_coords]

        def update(second, third, key):
            outer = bisect_left(second_coords, second) + 1
            while outer <= outer_size:
                coords = inner_coords[outer]
                inner = bisect_left(coords, third) + 1
                tree = trees[outer]
                while inner < len(tree):
                    old = tree[inner]
                    if old is None or key > old:
                        tree[inner] = key
                    inner += inner & -inner
                outer += outer & -outer

        def query(second, third):
            outer = bisect_right(second_coords, second)
            result = None
            while outer:
                coords = inner_coords[outer]
                inner = bisect_right(coords, third)
                tree = trees[outer]
                while inner:
                    value = tree[inner]
                    if value is not None and (result is None or value > result):
                        result = value
                    inner -= inner & -inner
                outer -= outer & -outer
            return result

        queries = sorted(left, key=lambda entry: capacities[0] - entry[0][0])
        point = 0
        for left_cost, left_value in queries:
            cap0 = capacities[0] - left_cost[0]
            while point < len(right) and right[point][0][0] <= cap0:
                cost, value = right[point]
                update(cost[1], cost[2], (value, -cost[0], -cost[1], -cost[2]))
                point += 1
            candidate = query(
                capacities[1] - left_cost[1], capacities[2] - left_cost[2]
            )
            if candidate is not None:
                total = (
                    left_value + candidate[0],
                    candidate[1] - left_cost[0],
                    candidate[2] - left_cost[1],
                    candidate[3] - left_cost[2],
                )
                if best is None or total > best:
                    best = total

    # The empty subset guarantees that a result exists.
    return best[0], tuple(-best[d + 1] for d in range(dimensions))


def _can_make_exact(items, target, dimensions):
    """Meet-in-the-middle feasibility for an exact (value, costs...) sum."""
    if any(target[d + 1] < 0 for d in range(dimensions)):
        return False
    if not items:
        return all(x == 0 for x in target)

    midpoint = len(items) // 2
    zero = (0,) * (dimensions + 1)
    first = {zero}
    for _index, value, cost, _mask in items[:midpoint]:
        vector = (value,) + cost
        additions = set()
        for old in first:
            new = tuple(old[d] + vector[d] for d in range(dimensions + 1))
            if all(new[d + 1] <= target[d + 1] for d in range(dimensions)):
                additions.add(new)
        first.update(additions)

    second = [zero]
    for _index, value, cost, _mask in items[midpoint:]:
        vector = (value,) + cost
        additions = []
        for old in second:
            new = tuple(old[d] + vector[d] for d in range(dimensions + 1))
            if all(new[d + 1] <= target[d + 1] for d in range(dimensions)):
                additions.append(new)
        second.extend(additions)

    for value in second:
        needed = tuple(target[d] - value[d] for d in range(dimensions + 1))
        if needed in first:
            return True
    return False


def _lexicographic_exact_mask(items, target_value, target_cost, fixed_mask, n, dimensions):
    """Find the lexicographically first full ID tuple with exact totals.

    Project indices are in ID order.  At each step the first ID that can be
    the next selected ID is forced.  Exact-sum feasibility of the suffix is
    answered by meet-in-the-middle.
    """
    by_index = {item[0]: item for item in items}
    available_mask = 0
    for index, _value, _cost, _mask in items:
        available_mask |= 1 << index

    remaining = (target_value,) + tuple(target_cost)
    fixed = fixed_mask
    position = 0

    while True:
        fixed_later = fixed & ~((1 << position) - 1)
        if not fixed_later and all(x == 0 for x in remaining):
            break

        found = False
        for index in range(position, n):
            bit = 1 << index
            suffix = [item for item in items if item[0] > index]
            if fixed & bit:
                # Optional IDs before this forced ID have already been shown
                # unable to begin a feasible continuation.
                if not _can_make_exact(suffix, remaining, dimensions):
                    raise RuntimeError("internal exact-reconstruction failure")
                position = index + 1
                found = True
                break
            if available_mask & bit:
                _item_index, value, cost, item_mask = by_index[index]
                candidate = (remaining[0] - value,) + tuple(
                    remaining[d + 1] - cost[d] for d in range(dimensions)
                )
                if _can_make_exact(suffix, candidate, dimensions):
                    fixed |= item_mask
                    remaining = candidate
                    position = index + 1
                    found = True
                    break

        if not found:
            raise RuntimeError("internal exact-reconstruction failure")

    return fixed


def _solve_independent(items, capacities, fixed_mask, n, dimensions):
    """Solve independent remaining projects, including the exact ID tie-break."""
    value, cost = _best_independent_primary(items, capacities, dimensions)
    mask = _lexicographic_exact_mask(items, value, cost, fixed_mask, n, dimensions)
    return mask, value, cost


def solve(projects, budget, required=()):
    """Return the globally optimal feasible portfolio described in TASK.md."""
    records, budget, direct_requires, closures, required_mask = _validate(
        projects, budget, required
    )
    n = len(records)
    dimensions = len(budget)
    if n == 0:
        return {"selected": [], "value": 0, "cost": [0] * dimensions}

    ids = [record[0] for record in records]
    values = [record[1] for record in records]
    costs = [record[2] for record in records]
    full_mask = (1 << n) - 1
    # A negative optional project can never be maximal in an optimum: removing
    # it alone preserves dependency closure and improves value.  The same is
    # true of a zero-value project with positive cost.  Such projects remain
    # available as dependencies, but never need their own branch decision.
    selectable_mask = 0
    for i in range(n):
        if values[i] > 0 or values[i] == 0 and not any(costs[i]):
            selectable_mask |= 1 << i

    conflict = [0] * n
    index = {project_id: i for i, project_id in enumerate(ids)}
    for i, record in enumerate(records):
        for other_id in record[4]:
            other = index[other_id]
            conflict[i] |= 1 << other
            conflict[other] |= 1 << i

    dependents = [0] * n
    for project in range(n):
        mask = closures[project]
        while mask:
            bit = mask & -mask
            dependents[bit.bit_length() - 1] |= 1 << project
            mask ^= bit

    aggregate_cache = {0: (0, (0,) * dimensions)}

    def aggregate(mask):
        cached = aggregate_cache.get(mask)
        if cached is not None:
            return cached
        total_value = 0
        total_cost = [0] * dimensions
        remaining = mask
        while remaining:
            bit = remaining & -remaining
            i = bit.bit_length() - 1
            total_value += values[i]
            for d in range(dimensions):
                total_cost[d] += costs[i][d]
            remaining ^= bit
        result = total_value, tuple(total_cost)
        aggregate_cache[mask] = result
        return result

    def conflict_union(mask):
        result = 0
        while mask:
            bit = mask & -mask
            result |= conflict[bit.bit_length() - 1]
            mask ^= bit
        return result

    def dependents_union(mask):
        result = 0
        while mask:
            bit = mask & -mask
            result |= dependents[bit.bit_length() - 1]
            mask ^= bit
        return result

    def has_conflict(mask):
        remaining = mask
        while remaining:
            bit = remaining & -remaining
            i = bit.bit_length() - 1
            if conflict[i] & (remaining ^ bit):
                return True
            remaining ^= bit
        return False

    # Mutual incompatibility between candidate projects includes conflicts in
    # either dependency closure, not just an exclusion written on the two
    # projects themselves.
    incompatible = [0] * n
    invalid = 0
    closure_conflicts = [conflict_union(mask) for mask in closures]
    for i in range(n):
        _closure_value, closure_cost = aggregate(closures[i])
        if has_conflict(closures[i]) or any(
            closure_cost[d] > budget[d] for d in range(dimensions)
        ):
            invalid |= 1 << i
        for j in range(i + 1, n):
            if closure_conflicts[i] & closures[j]:
                incompatible[i] |= 1 << j
                incompatible[j] |= 1 << i

    selected = 0
    mask = required_mask
    while mask:
        bit = mask & -mask
        selected |= closures[bit.bit_length() - 1]
        mask ^= bit

    if selected & invalid or has_conflict(selected):
        return None
    selected_value, selected_cost = aggregate(selected)
    if any(selected_cost[d] > budget[d] for d in range(dimensions)):
        return None

    # An invalid project, or a project whose closure conflicts with something
    # forced, can never be selected.  Banning all of its dependents performs
    # the contrapositive of the dependency implications immediately.
    banned_seed = invalid | conflict_union(selected)
    banned = dependents_union(banned_seed) & ~selected

    best_mask = selected
    best_value = selected_value
    best_cost = selected_cost

    tuple_cache = {}

    def id_tuple(mask):
        result = tuple_cache.get(mask)
        if result is None:
            result = tuple(ids[i] for i in range(n) if mask & (1 << i))
            tuple_cache[mask] = result
        return result

    def consider(mask, value, cost):
        nonlocal best_mask, best_value, best_cost
        if (
            value > best_value
            or value == best_value
            and (cost < best_cost or cost == best_cost and id_tuple(mask) < id_tuple(best_mask))
        ):
            best_mask = mask
            best_value = value
            best_cost = cost

    # Pre-sort positive projects for each one-dimensional fractional-knapsack
    # relaxation.  Zero-cost positive values are handled before these lists.
    ratio_orders = []
    for d in range(dimensions):
        candidates = [i for i in range(n) if values[i] > 0 and costs[i][d] > 0]

        def compare(a, b, dimension=d):
            left = values[a] * costs[b][dimension]
            right = values[b] * costs[a][dimension]
            if left != right:
                return -1 if left > right else 1
            return a - b

        ratio_orders.append(tuple(sorted(candidates, key=cmp_to_key(compare))))

    def clique_bound(available):
        positive = [i for i in range(n) if available & (1 << i) and values[i] > 0]
        if len(positive) < 2:
            return sum(values[i] for i in positive)

        orders = (
            sorted(
                positive,
                key=lambda i: (-(incompatible[i] & available).bit_count(), -values[i], i),
            ),
            sorted(positive, key=lambda i: (-values[i], i)),
        )
        best_bound = sum(values[i] for i in positive)
        for order in orders:
            cliques = []  # [member mask, greatest member value]
            for i in order:
                bit = 1 << i
                choice = None
                choice_key = None
                for position, (members, greatest) in enumerate(cliques):
                    if incompatible[i] & members == members:
                        increase = max(greatest, values[i]) - greatest
                        key = (increase, -members.bit_count())
                        if choice_key is None or key < choice_key:
                            choice_key = key
                            choice = position
                if choice is None:
                    cliques.append([bit, values[i]])
                else:
                    cliques[choice][0] |= bit
                    if values[i] > cliques[choice][1]:
                        cliques[choice][1] = values[i]
            bound = sum(greatest for _members, greatest in cliques)
            if bound < best_bound:
                best_bound = bound
        return best_bound

    def upper_value(value, cost, available):
        positive_sum = sum(
            values[i] for i in range(n) if available & (1 << i) and values[i] > 0
        )
        bound = min(positive_sum, clique_bound(available))
        for d in range(dimensions):
            capacity = budget[d] - cost[d]
            relaxed = sum(
                values[i]
                for i in range(n)
                if available & (1 << i) and values[i] > 0 and costs[i][d] == 0
            )
            remaining_capacity = capacity
            for i in ratio_orders[d]:
                if not available & (1 << i):
                    continue
                item_cost = costs[i][d]
                if item_cost <= remaining_capacity:
                    relaxed += values[i]
                    remaining_capacity -= item_cost
                else:
                    relaxed += values[i] * remaining_capacity // item_cost
                    break
            if relaxed < bound:
                bound = relaxed
        return value + bound

    def normalize(selected_mask, banned_mask, value, cost):
        """Ban every project whose required closure can no longer fit."""
        available = selectable_mask & ~(selected_mask | banned_mask)
        newly_impossible = 0
        remaining = available
        while remaining:
            bit = remaining & -remaining
            i = bit.bit_length() - 1
            addition = closures[i] & ~selected_mask
            if addition & banned_mask:
                newly_impossible |= bit
            else:
                _extra_value, extra_cost = aggregate(addition)
                if any(cost[d] + extra_cost[d] > budget[d] for d in range(dimensions)):
                    newly_impossible |= bit
            remaining ^= bit
        if newly_impossible:
            banned_mask |= dependents_union(newly_impossible)
        return banned_mask & ~selected_mask

    seen = set()

    def search(selected_mask, banned_mask, value, cost):
        nonlocal best_mask, best_value, best_cost
        banned_mask = normalize(selected_mask, banned_mask, value, cost)
        state = (selected_mask, banned_mask)
        if state in seen:
            return
        seen.add(state)

        consider(selected_mask, value, cost)
        available = selectable_mask & ~(selected_mask | banned_mask)
        if not available:
            return

        upper = upper_value(value, cost, available)
        if upper < best_value:
            return
        if upper == best_value:
            if cost > best_cost:
                return
            if cost == best_cost:
                if selected_mask:
                    last = selected_mask.bit_length() - 1
                    possible_additions = 0
                    possible = available
                    while possible:
                        possible_bit = possible & -possible
                        possible_additions |= closures[possible_bit.bit_length() - 1]
                        possible ^= possible_bit
                    optimistic = selected_mask | (
                        possible_additions & ((1 << last) - 1)
                    )
                else:
                    optimistic = 0
                if id_tuple(optimistic) >= id_tuple(best_mask):
                    return

        # Once no remaining implication or incompatibility joins two projects,
        # finish the whole subtree with the exact meet-in-the-middle routine.
        independent = True
        additions_seen = 0
        overlap_nodes = 0
        independent_items = []
        remaining = available
        while remaining:
            bit = remaining & -remaining
            i = bit.bit_length() - 1
            addition = closures[i] & ~selected_mask
            overlap_nodes |= addition & additions_seen
            if overlap_nodes or incompatible[i] & available:
                independent = False
            additions_seen |= addition
            extra_value, extra_cost = aggregate(addition)
            independent_items.append(
                ((addition & -addition).bit_length() - 1, extra_value, extra_cost, addition)
            )
            remaining ^= bit
        if independent:
            capacities = tuple(budget[d] - cost[d] for d in range(dimensions))
            # The first field is the bundle's smallest project index, the
            # point at which selecting that bundle first affects ID ordering.
            items = sorted(independent_items, key=lambda item: item[0])
            completion, extra_value, extra_cost = _solve_independent(
                items, capacities, selected_mask, n, dimensions
            )
            total_cost = tuple(cost[d] + extra_cost[d] for d in range(dimensions))
            consider(completion, value + extra_value, total_cost)
            return

        # Strong branching on a project whose decision propagates through many
        # implications or incompatibilities usually exposes independent pieces
        # after only a few levels.
        pivot = None
        pivot_key = None
        # A shared, not-yet-selected dependency is often the best branching
        # variable even when its own value is negative: including it separates
        # all of its children, while excluding it rejects them together.
        pivot_pool = overlap_nodes & ~(selected_mask | banned_mask)
        if not pivot_pool:
            pivot_pool = available
        remaining = pivot_pool
        while remaining:
            bit = remaining & -remaining
            i = bit.bit_length() - 1
            force_count = (closures[i] & available).bit_count()
            reject_count = (dependents[i] & available).bit_count()
            conflict_count = (incompatible[i] & available).bit_count()
            key = (force_count + reject_count + 2 * conflict_count, conflict_count, -i)
            if pivot_key is None or key > pivot_key:
                pivot_key = key
                pivot = i
            remaining ^= bit

        bit = 1 << pivot
        addition = closures[pivot] & ~selected_mask
        include_state = None
        if not addition & banned_mask:
            new_selected = selected_mask | addition
            if not has_conflict(new_selected):
                extra_value, extra_cost = aggregate(addition)
                new_cost = tuple(cost[d] + extra_cost[d] for d in range(dimensions))
                if all(new_cost[d] <= budget[d] for d in range(dimensions)):
                    new_banned = banned_mask | dependents_union(conflict_union(addition))
                    new_banned &= ~new_selected
                    include_state = (
                        new_selected,
                        new_banned,
                        value + extra_value,
                        new_cost,
                    )

        exclude_state = (
            selected_mask,
            (banned_mask | dependents[pivot]) & ~selected_mask,
            value,
            cost,
        )

        # Try the side with the larger cheap upper estimate first to establish
        # a strong incumbent early.  Ties favor inclusion of the earlier ID.
        if include_state is not None:
            include_available = selectable_mask & ~(include_state[0] | include_state[1])
            exclude_available = selectable_mask & ~(exclude_state[0] | exclude_state[1])
            include_hint = include_state[2] + sum(
                max(values[i], 0)
                for i in range(n)
                if include_available & (1 << i)
            )
            exclude_hint = exclude_state[2] + sum(
                max(values[i], 0)
                for i in range(n)
                if exclude_available & (1 << i)
            )
            if include_hint >= exclude_hint:
                search(*include_state)
                search(*exclude_state)
            else:
                search(*exclude_state)
                search(*include_state)
        else:
            search(*exclude_state)

    search(selected, banned, selected_value, selected_cost)
    return {
        "selected": list(id_tuple(best_mask)),
        "value": best_value,
        "cost": list(best_cost),
    }
