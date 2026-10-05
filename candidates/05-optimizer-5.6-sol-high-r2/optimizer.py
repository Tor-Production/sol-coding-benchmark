"""Exact constrained portfolio optimization.

The instance is small enough (at most 32 projects) that integer bit masks are
particularly useful.  The search below is exhaustive in principle, but uses
dependency propagation, conflict propagation, and fractional-knapsack bounds
to avoid visiting most subsets in structured instances.
"""

from functools import cmp_to_key


_PROJECT_KEYS = {"id", "value", "cost", "requires", "excludes"}


def _integer(value):
    """Return whether *value* is an integer permitted by the contract."""
    return isinstance(value, int) and not isinstance(value, bool)


def solve(projects, budget, required=()):
    """Return the globally best feasible portfolio, or ``None``.

    Inputs are validated before any optimization or early infeasibility return.
    The input objects are only read; all working data is newly allocated.
    """
    if not isinstance(projects, list) or len(projects) > 32:
        raise ValueError("projects must be a list containing at most 32 items")
    if not isinstance(budget, list) or not 1 <= len(budget) <= 3:
        raise ValueError("budget must be a list with one to three dimensions")
    if any(not _integer(x) or x < 0 for x in budget):
        raise ValueError("budget entries must be nonnegative integers")
    if not isinstance(required, (list, tuple)):
        raise ValueError("required must be a list or tuple")

    dimensions = len(budget)
    ids = []
    values = []
    costs = []
    requires_names = []
    excludes_names = []

    for project in projects:
        if not isinstance(project, dict) or set(project) != _PROJECT_KEYS:
            raise ValueError("each project must be a dictionary with exact keys")
        name = project["id"]
        value = project["value"]
        cost = project["cost"]
        requires_list = project["requires"]
        excludes_list = project["excludes"]
        if not isinstance(name, str) or not name:
            raise ValueError("project IDs must be nonempty strings")
        if not _integer(value):
            raise ValueError("project values must be integers")
        if not isinstance(cost, list) or len(cost) != dimensions:
            raise ValueError("project costs must match the budget dimensions")
        if any(not _integer(x) or x < 0 for x in cost):
            raise ValueError("project costs must be nonnegative integers")
        if not isinstance(requires_list, list) or not isinstance(excludes_list, list):
            raise ValueError("requires and excludes must be lists")
        for refs in (requires_list, excludes_list):
            if any(not isinstance(x, str) or not x for x in refs):
                raise ValueError("references must be nonempty string IDs")
            if len(set(refs)) != len(refs):
                raise ValueError("references within a list must be distinct")
            if name in refs:
                raise ValueError("self-references are not allowed")

        ids.append(name)
        values.append(value)
        costs.append(tuple(cost))
        requires_names.append(tuple(requires_list))
        excludes_names.append(tuple(excludes_list))

    if len(set(ids)) != len(ids):
        raise ValueError("project IDs must be unique")

    index = {name: i for i, name in enumerate(ids)}
    known = set(index)
    for refs in requires_names + excludes_names:
        if any(name not in known for name in refs):
            raise ValueError("all references must name known projects")

    if any(not isinstance(name, str) or not name for name in required):
        raise ValueError("required IDs must be nonempty strings")
    if len(set(required)) != len(required):
        raise ValueError("required IDs must be distinct")
    if any(name not in known for name in required):
        raise ValueError("required IDs must name known projects")

    n = len(projects)
    direct_requires = [0] * n
    conflicts = [0] * n
    for i in range(n):
        for name in requires_names[i]:
            direct_requires[i] |= 1 << index[name]
        for name in excludes_names[i]:
            j = index[name]
            conflicts[i] |= 1 << j
            conflicts[j] |= 1 << i

    # A three-colour DFS both validates acyclicity and computes dependency
    # closures.  At n <= 32 recursion depth is harmless.
    colour = [0] * n
    closures = [0] * n

    def dependency_closure(i):
        if colour[i] == 1:
            raise ValueError("the dependency graph must be acyclic")
        if colour[i] == 2:
            return closures[i]
        colour[i] = 1
        result = 1 << i
        bits = direct_requires[i]
        while bits:
            bit = bits & -bits
            bits ^= bit
            result |= dependency_closure(bit.bit_length() - 1)
        colour[i] = 2
        closures[i] = result
        return result

    for i in range(n):
        dependency_closure(i)

    all_mask = (1 << n) - 1

    def mask_totals(mask):
        value = 0
        total = [0] * dimensions
        while mask:
            bit = mask & -mask
            mask ^= bit
            i = bit.bit_length() - 1
            value += values[i]
            for d in range(dimensions):
                total[d] += costs[i][d]
        return value, tuple(total)

    def conflict_union(mask):
        result = 0
        while mask:
            bit = mask & -mask
            mask ^= bit
            result |= conflicts[bit.bit_length() - 1]
        return result

    # Closures containing an excluded pair can never be selected.
    closure_ok = [not bool(conflict_union(mask) & mask) for mask in closures]

    selected = 0
    for name in required:
        selected |= closures[index[name]]
    selected_value, selected_cost = mask_totals(selected)
    if conflict_union(selected) & selected or any(
        selected_cost[d] > budget[d] for d in range(dimensions)
    ):
        return None

    # Ratio orders are calculated exactly with cross multiplication.  They are
    # reused at every node for fractional upper/lower relaxations.
    positive = [i for i in range(n) if values[i] > 0]

    def upper_ratio_cmp(d):
        def compare(i, j):
            ci, cj = costs[i][d], costs[j][d]
            if ci == 0 or cj == 0:
                if ci == cj:
                    return (ids[i] > ids[j]) - (ids[i] < ids[j])
                return -1 if ci == 0 else 1
            left = values[i] * cj
            right = values[j] * ci
            if left != right:
                return -1 if left > right else 1
            return (ids[i] > ids[j]) - (ids[i] < ids[j])
        return compare

    upper_orders = [
        sorted(positive, key=cmp_to_key(upper_ratio_cmp(d)))
        for d in range(dimensions)
    ]

    def lower_ratio_cmp(d):
        def compare(i, j):
            left = costs[i][d] * values[j]
            right = costs[j][d] * values[i]
            if left != right:
                return -1 if left < right else 1
            return (ids[i] > ids[j]) - (ids[i] < ids[j])
        return compare

    lower_orders = [
        sorted(positive, key=cmp_to_key(lower_ratio_cmp(d)))
        for d in range(dimensions)
    ]

    lexical_indices = sorted(range(n), key=lambda i: ids[i])

    def id_tuple(mask):
        return tuple(ids[i] for i in lexical_indices if mask & (1 << i))

    best_mask = selected
    best_value = selected_value
    best_cost = selected_cost
    best_ids = id_tuple(selected)

    def consider(mask, value, total):
        nonlocal best_mask, best_value, best_cost, best_ids
        if value < best_value:
            return
        if value == best_value and total > best_cost:
            return
        names = id_tuple(mask)
        if (value > best_value or total < best_cost or
                (total == best_cost and names < best_ids)):
            best_mask = mask
            best_value = value
            best_cost = total
            best_ids = names

    def extra_totals(mask):
        value = 0
        total = [0] * dimensions
        while mask:
            bit = mask & -mask
            mask ^= bit
            i = bit.bit_length() - 1
            value += values[i]
            for d in range(dimensions):
                total[d] += costs[i][d]
        return value, tuple(total)

    def propagate(mask, unavailable, total):
        """Mark every project that provably cannot be added at this node."""
        while True:
            newly_unavailable = 0
            remaining = all_mask & ~(mask | unavailable)
            bits = remaining
            selected_conflicts = conflict_union(mask)
            while bits:
                bit = bits & -bits
                bits ^= bit
                i = bit.bit_length() - 1
                closure = closures[i]
                if (not closure_ok[i] or closure & unavailable or
                        closure & selected_conflicts):
                    newly_unavailable |= bit
                    continue
                delta = closure & ~mask
                _, added_cost = extra_totals(delta)
                if any(total[d] + added_cost[d] > budget[d]
                       for d in range(dimensions)):
                    newly_unavailable |= bit
            newly_unavailable &= ~unavailable
            if not newly_unavailable:
                return unavailable
            unavailable |= newly_unavailable

    def fractional_value_bound(available, total):
        """Integer ceiling of a safe multidimensional LP value bound."""
        bounds = []
        for d in range(dimensions):
            room = budget[d] - total[d]
            gain = 0
            for i in upper_orders[d]:
                if not available & (1 << i):
                    continue
                cost = costs[i][d]
                if cost == 0:
                    gain += values[i]
                elif cost <= room:
                    gain += values[i]
                    room -= cost
                else:
                    gain += (values[i] * room + cost - 1) // cost
                    break
            bounds.append(gain)
        return min(bounds)

    # When negative-valued dependencies exist, a raw fractional-knapsack bound
    # can be very loose.  Maximum-weight closure is a second relaxation: it
    # enforces all dependency implications but deliberately ignores costs and
    # exclusions.  The classic source/sink min-cut construction solves it.
    use_closure_bound = bool(
        any(value < 0 for value in values) and any(direct_requires)
    )
    closure_bound_cache = {}

    def maximum_closure_bound(available):
        cached = closure_bound_cache.get(available)
        if cached is not None:
            return cached
        positive_total = sum(
            values[i] for i in range(n)
            if available & (1 << i) and values[i] > 0
        )
        if positive_total == 0:
            closure_bound_cache[available] = 0
            return 0

        source, sink = n, n + 1
        graph = [[] for _ in range(n + 2)]

        def add_edge(start, end, capacity):
            graph[start].append([end, len(graph[end]), capacity])
            graph[end].append([start, len(graph[start]) - 1, 0])

        infinity = positive_total + 1
        bits = available
        while bits:
            bit = bits & -bits
            bits ^= bit
            i = bit.bit_length() - 1
            if values[i] > 0:
                add_edge(source, i, values[i])
            elif values[i] < 0:
                add_edge(i, sink, -values[i])
            dependencies = direct_requires[i] & available
            while dependencies:
                dep = dependencies & -dependencies
                dependencies ^= dep
                add_edge(i, dep.bit_length() - 1, infinity)

        flow = 0
        while True:
            level = [-1] * (n + 2)
            level[source] = 0
            queue = [source]
            for node in queue:
                for end, _, capacity in graph[node]:
                    if capacity and level[end] < 0:
                        level[end] = level[node] + 1
                        queue.append(end)
            if level[sink] < 0:
                break
            next_edge = [0] * (n + 2)

            def send(node, amount):
                if node == sink:
                    return amount
                while next_edge[node] < len(graph[node]):
                    edge = graph[node][next_edge[node]]
                    end, reverse, capacity = edge
                    if capacity and level[end] == level[node] + 1:
                        pushed = send(end, min(amount, capacity))
                        if pushed:
                            edge[2] -= pushed
                            graph[end][reverse][2] += pushed
                            return pushed
                    next_edge[node] += 1
                return 0

            while True:
                pushed = send(source, positive_total + 1)
                if not pushed:
                    break
                flow += pushed

        result = positive_total - flow
        closure_bound_cache[available] = result
        return result

    def fractional_cost_lower_bound(available, target, total):
        """Componentwise lower cost bound for gaining ``target`` value."""
        if target <= 0:
            return total
        result = []
        for d in range(dimensions):
            needed = target
            addition = 0
            for i in lower_orders[d]:
                if not available & (1 << i):
                    continue
                value = values[i]
                if value <= needed:
                    addition += costs[i][d]
                    needed -= value
                else:
                    addition += (costs[i][d] * needed + value - 1) // value
                    needed = 0
                if needed == 0:
                    break
            if needed:
                return None
            result.append(total[d] + addition)
        return tuple(result)

    def relaxed_lexicographic_ids(mask, available, target):
        """Lexicographically least ID tuple in a relaxed value-only problem."""
        last_fixed = -1
        for position, i in enumerate(lexical_indices):
            if mask & (1 << i):
                last_fixed = position
        gain = 0
        answer = []
        for position, i in enumerate(lexical_indices):
            bit = 1 << i
            if mask & bit:
                answer.append(ids[i])
            elif available & bit:
                if position < last_fixed or gain < target:
                    answer.append(ids[i])
                    if values[i] > 0:
                        gain += values[i]
        return tuple(answer)

    # Static branching priorities.  Dependencies/conflicts are handled first;
    # otherwise positive density and ID order quickly establish a good incumbent.
    reverse_counts = [0] * n
    for closure in closures:
        bits = closure
        while bits:
            bit = bits & -bits
            bits ^= bit
            reverse_counts[bit.bit_length() - 1] += 1

    constraint_scores = [
        reverse_counts[i] - 1 + conflicts[i].bit_count() for i in range(n)
    ]

    def branch_compare(i, j):
        if constraint_scores[i] != constraint_scores[j]:
            return -1 if constraint_scores[i] > constraint_scores[j] else 1
        # Within an equally constrained group, use an exact value/cost density
        # comparison.  No conversion to float is made, even for huge integers.
        positive_i, positive_j = values[i] > 0, values[j] > 0
        if positive_i != positive_j:
            return -1 if positive_i else 1
        ci, cj = sum(costs[i]), sum(costs[j])
        if positive_i:
            if ci == 0 or cj == 0:
                if ci != cj:
                    return -1 if ci == 0 else 1
                left, right = values[i], values[j]
            else:
                left, right = values[i] * cj, values[j] * ci
            if left != right:
                return -1 if left > right else 1
        if values[i] != values[j]:
            return -1 if values[i] > values[j] else 1
        if ci != cj:
            return -1 if ci < cj else 1
        return (ids[i] > ids[j]) - (ids[i] < ids[j])

    branch_order = sorted(range(n), key=cmp_to_key(branch_compare))
    visited = set()

    def search(mask, unavailable, value, total):
        nonlocal best_value, best_cost, best_ids
        unavailable = propagate(mask, unavailable, total)
        state = (mask, unavailable)
        if state in visited:
            return
        visited.add(state)

        consider(mask, value, total)
        available = all_mask & ~(mask | unavailable)
        if not available:
            return

        possible_gain = fractional_value_bound(available, total)
        if use_closure_bound:
            possible_gain = min(possible_gain, maximum_closure_bound(available))
        upper = value + possible_gain
        if upper < best_value:
            return
        if upper == best_value:
            lower_cost = fractional_cost_lower_bound(
                available, best_value - value, total
            )
            if lower_cost is None or lower_cost > best_cost:
                return
            if lower_cost == best_cost:
                optimistic_ids = relaxed_lexicographic_ids(
                    mask, available, best_value - value
                )
                if optimistic_ids >= best_ids:
                    return

        choice = next(i for i in branch_order if available & (1 << i))
        bit = 1 << choice
        closure = closures[choice]
        delta = closure & ~mask
        added_value, added_cost = extra_totals(delta)
        new_total = tuple(total[d] + added_cost[d] for d in range(dimensions))
        new_mask = mask | closure
        new_unavailable = unavailable | conflict_union(closure)

        # Explore the locally promising side first solely to improve bounds;
        # both sides are always searched unless safely pruned.
        if (added_value > 0 or reverse_counts[choice] > 1 or
                (added_value == 0 and new_total <= total)):
            search(new_mask, new_unavailable, value + added_value, new_total)
            search(mask, unavailable | bit, value, total)
        else:
            search(mask, unavailable | bit, value, total)
            search(new_mask, new_unavailable, value + added_value, new_total)

    initially_unavailable = conflict_union(selected)
    search(selected, initially_unavailable, selected_value, selected_cost)
    return {"selected": list(best_ids), "value": best_value, "cost": list(best_cost)}
