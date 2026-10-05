"""Exact constrained portfolio optimization.

The public entry point is :func:`solve`.  The implementation deliberately
uses bit masks: the problem is small in number of projects (at most 32), but
may have quite a lot of logical structure to propagate at each search node.
"""

from collections import deque
from functools import cmp_to_key


_PROJECT_KEYS = {"id", "value", "cost", "requires", "excludes"}


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _validate(projects, budget, required):
    """Validate and copy the input into immutable, ID-sorted arrays."""
    if not isinstance(projects, list) or len(projects) > 32:
        raise ValueError("projects must be a list containing at most 32 items")
    if not isinstance(budget, list) or not 1 <= len(budget) <= 3:
        raise ValueError("budget must be a list with one to three dimensions")
    if any(not _integer(x) or x < 0 for x in budget):
        raise ValueError("budget entries must be nonnegative integers")
    if not isinstance(required, (list, tuple)):
        raise ValueError("required must be a list or tuple")

    copied = []
    seen_ids = set()
    for project in projects:
        if not isinstance(project, dict) or set(project) != _PROJECT_KEYS:
            raise ValueError("each project must be a dictionary with exact keys")

        project_id = project["id"]
        value = project["value"]
        cost = project["cost"]
        requires = project["requires"]
        excludes = project["excludes"]

        if not isinstance(project_id, str) or not project_id:
            raise ValueError("project IDs must be nonempty strings")
        if project_id in seen_ids:
            raise ValueError("project IDs must be unique")
        seen_ids.add(project_id)
        if not _integer(value):
            raise ValueError("project values must be integers")
        if not isinstance(cost, list) or len(cost) != len(budget):
            raise ValueError("project costs must match the budget dimensions")
        if any(not _integer(x) or x < 0 for x in cost):
            raise ValueError("project costs must be nonnegative integers")
        if not isinstance(requires, list) or not isinstance(excludes, list):
            raise ValueError("requires and excludes must be lists")

        for references in (requires, excludes):
            if any(not isinstance(x, str) for x in references):
                raise ValueError("project references must be strings")
            if len(set(references)) != len(references):
                raise ValueError("project reference lists must not contain duplicates")
            if project_id in references:
                raise ValueError("projects cannot refer to themselves")

        # Tuples ensure that nothing below can mutate caller-owned nested lists.
        copied.append(
            (project_id, value, tuple(cost), tuple(requires), tuple(excludes))
        )

    for _, _, _, requires, excludes in copied:
        if any(x not in seen_ids for x in requires + excludes):
            raise ValueError("all project references must name known projects")

    if any(not isinstance(x, str) for x in required):
        raise ValueError("required project IDs must be strings")
    if len(set(required)) != len(required):
        raise ValueError("required project IDs must be distinct")
    if any(x not in seen_ids for x in required):
        raise ValueError("required project IDs must be known")

    copied.sort(key=lambda item: item[0])
    ids = tuple(item[0] for item in copied)
    id_to_index = {project_id: i for i, project_id in enumerate(ids)}
    values = tuple(item[1] for item in copied)
    costs = tuple(item[2] for item in copied)
    direct_requires = tuple(
        sum((1 << id_to_index[x] for x in item[3]), 0) for item in copied
    )
    declared_excludes = tuple(
        sum((1 << id_to_index[x] for x in item[4]), 0) for item in copied
    )

    # Exclusions are symmetric even when only one endpoint declares the edge.
    conflicts = list(declared_excludes)
    for i, mask in enumerate(declared_excludes):
        while mask:
            bit = mask & -mask
            j = bit.bit_length() - 1
            conflicts[j] |= 1 << i
            mask ^= bit

    # A DFS both checks the whole dependency graph for cycles and constructs
    # each transitive prerequisite closure.
    n = len(ids)
    state = [0] * n
    closure = [0] * n

    def visit(i):
        if state[i] == 1:
            raise ValueError("the dependency graph must be acyclic")
        if state[i] == 2:
            return closure[i]
        state[i] = 1
        result = 1 << i
        mask = direct_requires[i]
        while mask:
            bit = mask & -mask
            result |= visit(bit.bit_length() - 1)
            mask ^= bit
        state[i] = 2
        closure[i] = result
        return result

    for i in range(n):
        visit(i)

    required_mask = 0
    for project_id in required:
        required_mask |= closure[id_to_index[project_id]]

    return (
        ids,
        values,
        costs,
        tuple(direct_requires),
        tuple(conflicts),
        tuple(closure),
        tuple(budget),
        required_mask,
    )


def solve(projects, budget, required=()):
    """Return the globally best feasible portfolio, or ``None``.

    Search nodes represent all decisions made so far with two masks: selected
    projects and projects known to be impossible.  Prerequisites and conflicts
    are propagated immediately.  Several independent admissible upper bounds
    make the remaining exponential search small on structured instances.
    """
    (
        ids,
        values,
        costs,
        direct_requires,
        conflicts,
        closure,
        budget,
        selected_root,
    ) = _validate(projects, budget, required)

    n = len(ids)
    dimensions = len(budget)
    all_mask = (1 << n) - 1
    if n == 0:
        return {"selected": [], "value": 0, "cost": [0] * dimensions}

    def mask_metrics(mask):
        value = 0
        totals = [0] * dimensions
        while mask:
            bit = mask & -mask
            i = bit.bit_length() - 1
            value += values[i]
            row = costs[i]
            for d in range(dimensions):
                totals[d] += row[d]
            mask ^= bit
        return value, tuple(totals)

    def mask_ids(mask):
        return tuple(ids[i] for i in range(n) if mask & (1 << i))

    # reverse_closure[i] consists of every project that (transitively) needs i.
    # Therefore, rejecting i rejects this entire mask in one operation.
    reverse_closure = [0] * n
    for i, required_mask in enumerate(closure):
        mask = required_mask
        while mask:
            bit = mask & -mask
            reverse_closure[bit.bit_length() - 1] |= 1 << i
            mask ^= bit

    # ban[i] is the complete set made impossible by selecting i, including
    # dependents of projects conflicting with any prerequisite of i.
    ban = [0] * n
    for i in range(n):
        conflicting = 0
        mask = closure[i]
        while mask:
            bit = mask & -mask
            conflicting |= conflicts[bit.bit_length() - 1]
            mask ^= bit
        mask = conflicting
        result = 0
        while mask:
            bit = mask & -mask
            result |= reverse_closure[bit.bit_length() - 1]
            mask ^= bit
        ban[i] = result

    # Closures which conflict internally or can never fit are globally false.
    impossible = 0
    for i in range(n):
        required_mask = closure[i]
        internal_conflict = False
        mask = required_mask
        while mask:
            bit = mask & -mask
            if conflicts[bit.bit_length() - 1] & required_mask:
                internal_conflict = True
                break
            mask ^= bit
        _, closure_cost = mask_metrics(required_mask)
        if internal_conflict or any(
            closure_cost[d] > budget[d] for d in range(dimensions)
        ):
            impossible |= reverse_closure[i]

    selected_value, selected_cost = mask_metrics(selected_root)
    forbidden_root = impossible
    mask = selected_root
    while mask:
        bit = mask & -mask
        forbidden_root |= ban[bit.bit_length() - 1]
        mask ^= bit
    if selected_root & forbidden_root or any(
        selected_cost[d] > budget[d] for d in range(dimensions)
    ):
        return None

    positive_mask = sum(
        (1 << i for i, value in enumerate(values) if value > 0), 0
    )

    # Exact incompatibility (including conflicts reached through prerequisites)
    # is useful for both propagation and a weighted clique-cover upper bound.
    incompatibility = tuple(ban[i] & all_mask for i in range(n))

    def ratio_order(scalar_costs):
        candidates = [i for i in range(n) if values[i] > 0]

        def compare(i, j):
            ci, cj = scalar_costs[i], scalar_costs[j]
            if ci == 0 or cj == 0:
                if ci == cj:
                    if values[i] != values[j]:
                        return -1 if values[i] > values[j] else 1
                    return -1 if i < j else (1 if i > j else 0)
                return -1 if ci == 0 else 1
            left = values[i] * cj
            right = values[j] * ci
            if left != right:
                return -1 if left > right else 1
            if values[i] != values[j]:
                return -1 if values[i] > values[j] else 1
            return -1 if i < j else (1 if i > j else 0)

        return tuple(sorted(candidates, key=cmp_to_key(compare)))

    # Every nonnegative linear combination of resource constraints is another
    # valid knapsack relaxation.  A few useful combinations noticeably tighten
    # multidimensional instances while keeping each node inexpensive.
    weight_vectors = []
    for d in range(dimensions):
        weight_vectors.append(tuple(1 if e == d else 0 for e in range(dimensions)))
    for subset in range(1, 1 << dimensions):
        if subset & (subset - 1):
            weight_vectors.append(
                tuple(1 if subset & (1 << d) else 0 for d in range(dimensions))
            )
    scale = 1
    for amount in budget:
        scale *= max(1, amount)
    normalized = tuple(scale // max(1, amount) for amount in budget)
    weight_vectors.append(normalized)
    weight_vectors = tuple(dict.fromkeys(weight_vectors))

    relaxations = []
    for weights in weight_vectors:
        scalar_costs = tuple(
            sum(weights[d] * costs[i][d] for d in range(dimensions))
            for i in range(n)
        )
        relaxations.append((weights, scalar_costs, ratio_order(scalar_costs)))
    relaxations = tuple(relaxations)

    unit_orders = []
    for d in range(dimensions):
        scalar = tuple(costs[i][d] for i in range(n))
        unit_orders.append(ratio_order(scalar))

    def positive_sum(mask):
        result = 0
        mask &= positive_mask
        while mask:
            bit = mask & -mask
            result += values[bit.bit_length() - 1]
            mask ^= bit
        return result

    # Maximum-weight dependency closure, ignoring conflicts and budget.  This
    # is a min-cut computation and is often much sharper than summing positive
    # values when attractive projects carry negative prerequisites.
    closure_bound_cache = {}

    def dependency_upper(undecided):
        cached = closure_bound_cache.get(undecided)
        if cached is not None:
            return cached
        total_positive = positive_sum(undecided)
        if total_positive == 0:
            closure_bound_cache[undecided] = 0
            return 0

        has_edge = False
        mask = undecided
        while mask:
            bit = mask & -mask
            i = bit.bit_length() - 1
            if direct_requires[i] & undecided:
                has_edge = True
                break
            mask ^= bit
        if not has_edge:
            closure_bound_cache[undecided] = total_positive
            return total_positive

        source, sink = n, n + 1
        graph = [[] for _ in range(n + 2)]

        def add_edge(start, end, capacity):
            graph[start].append([end, capacity, len(graph[end])])
            graph[end].append([start, 0, len(graph[start]) - 1])

        infinite = total_positive + 1
        mask = undecided
        while mask:
            bit = mask & -mask
            i = bit.bit_length() - 1
            if values[i] > 0:
                add_edge(source, i, values[i])
            elif values[i] < 0:
                add_edge(i, sink, -values[i])
            requirements = direct_requires[i] & undecided
            while requirements:
                req_bit = requirements & -requirements
                add_edge(i, req_bit.bit_length() - 1, infinite)
                requirements ^= req_bit
            mask ^= bit

        flow = 0
        while True:
            level = [-1] * (n + 2)
            level[source] = 0
            queue = deque([source])
            while queue:
                node = queue.popleft()
                for end, capacity, _ in graph[node]:
                    if capacity and level[end] < 0:
                        level[end] = level[node] + 1
                        queue.append(end)
            if level[sink] < 0:
                break
            position = [0] * (n + 2)

            def send(node, amount):
                if node == sink:
                    return amount
                while position[node] < len(graph[node]):
                    edge = graph[node][position[node]]
                    end, capacity, reverse = edge
                    if capacity and level[end] == level[node] + 1:
                        pushed = send(end, min(amount, capacity))
                        if pushed:
                            edge[1] -= pushed
                            graph[end][reverse][1] += pushed
                            return pushed
                    position[node] += 1
                return 0

            while True:
                pushed = send(source, infinite)
                if not pushed:
                    break
                flow += pushed

        result = total_positive - flow
        closure_bound_cache[undecided] = result
        return result

    def clique_upper(undecided, current_upper):
        vertices = [i for i in range(n) if undecided & (1 << i) and values[i] > 0]
        if len(vertices) < 2:
            return current_upper
        if not any(incompatibility[i] & undecided & positive_mask for i in vertices):
            return current_upper

        orders = (
            sorted(
                vertices,
                key=lambda i: (
                    -(incompatibility[i] & undecided & positive_mask).bit_count(),
                    -values[i],
                    i,
                ),
            ),
            sorted(vertices, key=lambda i: (-values[i], i)),
        )
        best = current_upper
        for order in orders:
            groups = []  # [member mask, greatest value]
            for i in order:
                compatible_groups = []
                for g, (members, greatest) in enumerate(groups):
                    if members & ~incompatibility[i] == 0:
                        compatible_groups.append((max(0, values[i] - greatest), g))
                if compatible_groups:
                    _, g = min(compatible_groups)
                    groups[g][0] |= 1 << i
                    if values[i] > groups[g][1]:
                        groups[g][1] = values[i]
                else:
                    groups.append([1 << i, values[i]])
            best = min(best, sum(group[1] for group in groups))
        return best

    def extension_upper(undecided, current_cost):
        upper = positive_sum(undecided)
        upper = min(upper, dependency_upper(undecided))
        upper = clique_upper(undecided, upper)
        for weights, scalar_costs, order in relaxations:
            capacity = sum(
                weights[d] * (budget[d] - current_cost[d])
                for d in range(dimensions)
            )
            value = 0
            for i in order:
                if not undecided & (1 << i):
                    continue
                item_cost = scalar_costs[i]
                if item_cost == 0:
                    value += values[i]
                elif item_cost <= capacity:
                    value += values[i]
                    capacity -= item_cost
                else:
                    value += (values[i] * capacity) // item_cost
                    break
            if value < upper:
                upper = value
        return upper

    def minimum_added_costs(undecided, needed_value):
        """Componentwise fractional lower bounds for gaining needed_value."""
        if needed_value <= 0:
            return (0,) * dimensions
        result = []
        for d, order in enumerate(unit_orders):
            still_needed = needed_value
            amount = 0
            for i in order:
                if not undecided & (1 << i):
                    continue
                value = values[i]
                item_cost = costs[i][d]
                if value <= still_needed:
                    amount += item_cost
                    still_needed -= value
                    if still_needed == 0:
                        break
                else:
                    amount += (item_cost * still_needed + value - 1) // value
                    still_needed = 0
                    break
            if still_needed:
                return None
            result.append(amount)
        return tuple(result)

    def least_strict_superset(mask, undecided):
        """Lexicographic lower bound on every strict completion of mask."""
        if not undecided:
            return None
        if not mask:
            bit = undecided & -undecided
            return (ids[bit.bit_length() - 1],)
        greatest = mask.bit_length() - 1
        below = undecided & ((1 << greatest) - 1)
        if below:
            return mask_ids(mask | below)
        bit = undecided & -undecided
        return mask_ids(mask | bit)

    best_mask = selected_root
    best_value = selected_value
    best_cost = selected_cost
    best_ids = mask_ids(selected_root)

    def consider(mask, value, cost):
        nonlocal best_mask, best_value, best_cost, best_ids
        candidate_ids = None
        if value > best_value or (value == best_value and cost < best_cost):
            better = True
        elif value == best_value and cost == best_cost:
            candidate_ids = mask_ids(mask)
            better = candidate_ids < best_ids
        else:
            better = False
        if better:
            if candidate_ids is None:
                candidate_ids = mask_ids(mask)
            best_mask, best_value, best_cost, best_ids = (
                mask,
                value,
                cost,
                candidate_ids,
            )

    def normalize(mask, forbidden, cost):
        """Apply budget impossibility and safe terminal-project reductions."""
        while True:
            undecided = all_mask & ~(mask | forbidden)
            newly_forbidden = 0
            scan = undecided
            while scan:
                bit = scan & -scan
                i = bit.bit_length() - 1
                addition = closure[i] & ~mask
                _, added_cost = mask_metrics(addition)
                if any(cost[d] + added_cost[d] > budget[d] for d in range(dimensions)):
                    newly_forbidden |= reverse_closure[i]
                elif (
                    (values[i] < 0 or (values[i] == 0 and any(costs[i])))
                    and reverse_closure[i] & undecided == bit
                ):
                    # No undecided project needs this terminal item.  Omitting a
                    # negative item improves value; omitting a costly zero-value
                    # item improves the cost tie-break.
                    newly_forbidden |= bit
                scan ^= bit
            newly_forbidden &= undecided
            if not newly_forbidden:
                return forbidden
            forbidden |= newly_forbidden

    forbidden_root = normalize(selected_root, forbidden_root, selected_cost)

    # Seed the incumbent with several fast greedy completions.  These do not
    # affect correctness, but a good early lower bound is valuable to pruning.
    primary_order = list(relaxations[-1][2])
    primary_seen = set(primary_order)
    primary_order += [i for i in range(n) if i not in primary_seen]
    greedy_orders = [
        tuple(primary_order),
        tuple(sorted(range(n), key=lambda i: (-values[i], i))),
        tuple(range(n)),
    ]

    for order in greedy_orders:
        mask = selected_root
        forbidden = forbidden_root
        value = selected_value
        cost = selected_cost
        changed = True
        while changed:
            changed = False
            forbidden = normalize(mask, forbidden, cost)
            undecided = all_mask & ~(mask | forbidden)
            for i in order:
                bit = 1 << i
                if not undecided & bit:
                    continue
                addition = closure[i] & ~mask
                added_value, added_cost = mask_metrics(addition)
                next_cost = tuple(cost[d] + added_cost[d] for d in range(dimensions))
                if added_value > 0 and all(
                    next_cost[d] <= budget[d] for d in range(dimensions)
                ):
                    mask |= addition
                    forbidden |= ban[i]
                    value += added_value
                    cost = next_cost
                    consider(mask, value, cost)
                    changed = True
                    break
        # Free, value-neutral additions can matter only to the final ID tie.
        for i in range(n):
            bit = 1 << i
            if mask & bit or forbidden & bit:
                continue
            addition = closure[i] & ~mask
            added_value, added_cost = mask_metrics(addition)
            if added_value == 0 and not any(added_cost):
                next_mask = mask | addition
                if mask_ids(next_mask) < mask_ids(mask):
                    mask = next_mask
                    forbidden |= ban[i]
                    consider(mask, value, cost)

    # Rank supplies a density-oriented tiebreak when propagation scores do not
    # distinguish branch variables.
    pivot_rank = {i: rank for rank, i in enumerate(primary_order)}
    visited = set()

    def search(mask, forbidden, value, cost):
        forbidden = normalize(mask, forbidden, cost)
        state_key = (mask, forbidden)
        if state_key in visited:
            return
        visited.add(state_key)

        consider(mask, value, cost)
        undecided = all_mask & ~(mask | forbidden)
        if not undecided:
            return

        upper = value + extension_upper(undecided, cost)
        if upper < best_value:
            return
        if upper == best_value:
            added_cost_lb = minimum_added_costs(
                undecided, best_value - value
            )
            if added_cost_lb is None:
                return
            final_cost_lb = tuple(
                cost[d] + added_cost_lb[d] for d in range(dimensions)
            )
            if final_cost_lb > best_cost:
                return
            if final_cost_lb == best_cost:
                selection_lb = least_strict_superset(mask, undecided)
                if selection_lb is None or selection_lb >= best_ids:
                    return

        # Choose a variable that makes both branches propagate as much as
        # possible.  Density decides unstructured knapsack ties.
        pivot = None
        pivot_key = None
        scan = undecided
        while scan:
            bit = scan & -scan
            i = bit.bit_length() - 1
            include_count = ((closure[i] | ban[i]) & undecided).bit_count()
            exclude_count = (reverse_closure[i] & undecided).bit_count()
            key = (
                include_count * exclude_count,
                include_count + exclude_count,
                -pivot_rank.get(i, n + i),
                -i,
            )
            if pivot_key is None or key > pivot_key:
                pivot, pivot_key = i, key
            scan ^= bit

        addition = closure[pivot] & ~mask
        added_value, added_cost = mask_metrics(addition)
        include_cost = tuple(cost[d] + added_cost[d] for d in range(dimensions))
        include_mask = mask | addition
        include_forbidden = forbidden | ban[pivot]
        exclude_forbidden = forbidden | reverse_closure[pivot]

        include_undecided = all_mask & ~(include_mask | include_forbidden)
        exclude_undecided = all_mask & ~(mask | exclude_forbidden)
        include_promise = value + added_value + positive_sum(include_undecided)
        exclude_promise = value + positive_sum(exclude_undecided)

        include_branch = (
            include_mask,
            include_forbidden,
            value + added_value,
            include_cost,
        )
        exclude_branch = (mask, exclude_forbidden, value, cost)
        if include_promise > exclude_promise:
            branches = (include_branch, exclude_branch)
        elif include_promise < exclude_promise:
            branches = (exclude_branch, include_branch)
        else:
            # This is particularly useful for zero-value/free lexicographic
            # choices.  Both states are feasible partial portfolios.
            if mask_ids(include_mask) < mask_ids(mask):
                branches = (include_branch, exclude_branch)
            else:
                branches = (exclude_branch, include_branch)
        for branch in branches:
            search(*branch)

    search(selected_root, forbidden_root, selected_value, selected_cost)
    return {"selected": list(best_ids), "value": best_value, "cost": list(best_cost)}
