"""Exact constrained portfolio optimization using only the standard library."""

from bisect import bisect_left, bisect_right
from collections import deque
from functools import cmp_to_key


_PROJECT_KEYS = {"id", "value", "cost", "requires", "excludes"}


def _is_int(value):
    """The contract calls bool invalid even though it subclasses int."""
    return isinstance(value, int) and not isinstance(value, bool)


def solve(projects, budget, required=()):
    """Return the globally best feasible portfolio, or ``None``."""

    # Validate and make private, immutable copies of all input data.
    if not isinstance(projects, list) or len(projects) > 32:
        raise ValueError("projects must be a list containing at most 32 items")
    if not isinstance(budget, list) or not 1 <= len(budget) <= 3:
        raise ValueError("budget must be a list with one to three dimensions")
    if any(not _is_int(x) or x < 0 for x in budget):
        raise ValueError("budget entries must be nonnegative integers")
    budget = tuple(budget)
    dimensions = len(budget)

    records = []
    known_ids = set()
    for project in projects:
        if not isinstance(project, dict) or set(project) != _PROJECT_KEYS:
            raise ValueError("each project must have exactly the specified keys")
        project_id = project["id"]
        value = project["value"]
        cost = project["cost"]
        requires_refs = project["requires"]
        excludes_refs = project["excludes"]
        if not isinstance(project_id, str) or not project_id:
            raise ValueError("project IDs must be nonempty strings")
        if project_id in known_ids:
            raise ValueError("project IDs must be unique")
        known_ids.add(project_id)
        if not _is_int(value):
            raise ValueError("project values must be integers (not bool)")
        if not isinstance(cost, list) or len(cost) != dimensions:
            raise ValueError("project costs must match the budget dimensions")
        if any(not _is_int(x) or x < 0 for x in cost):
            raise ValueError("project costs must be nonnegative integers")
        if not isinstance(requires_refs, list) or not isinstance(excludes_refs, list):
            raise ValueError("requires and excludes must be lists")

        # Copy nested containers before doing any optimization.
        requires_copy = tuple(requires_refs)
        excludes_copy = tuple(excludes_refs)
        for refs in (requires_copy, excludes_copy):
            if any(not isinstance(x, str) for x in refs):
                raise ValueError("references must be project IDs")
            if len(set(refs)) != len(refs):
                raise ValueError("duplicate project reference")
        records.append((project_id, value, tuple(cost), requires_copy, excludes_copy))

    if not isinstance(required, (list, tuple)):
        raise ValueError("required must be a list or tuple")
    required = tuple(required)
    if any(not isinstance(x, str) for x in required) or len(set(required)) != len(required):
        raise ValueError("required IDs must be distinct strings")

    for project_id, _value, _cost, requires_refs, excludes_refs in records:
        for ref in requires_refs + excludes_refs:
            if ref == project_id:
                raise ValueError("self references are invalid")
            if ref not in known_ids:
                raise ValueError("unknown project reference")
    if any(x not in known_ids for x in required):
        raise ValueError("unknown required project ID")

    # Mask order is Python string order, which simplifies the final tie-break.
    records.sort(key=lambda item: item[0])
    ids = tuple(item[0] for item in records)
    n = len(records)
    index = {project_id: i for i, project_id in enumerate(ids)}
    values = tuple(item[1] for item in records)
    costs = tuple(item[2] for item in records)
    direct_requirements = tuple(
        sum(1 << index[ref] for ref in item[3]) for item in records
    )

    # A three-colour DFS rejects cycles and computes transitive closures.
    colours = [0] * n
    closures = [0] * n

    def visit(i):
        if colours[i] == 1:
            raise ValueError("dependency graph must be acyclic")
        if colours[i] == 2:
            return closures[i]
        colours[i] = 1
        result = 1 << i
        refs = direct_requirements[i]
        while refs:
            bit = refs & -refs
            refs -= bit
            result |= visit(bit.bit_length() - 1)
        colours[i] = 2
        closures[i] = result
        return result

    for i in range(n):
        visit(i)
    closures = tuple(closures)

    # Exclusions are symmetric even when declared on one side only.
    conflicts = [0] * n
    for project_id, _value, _cost, _requires, excludes_refs in records:
        i = index[project_id]
        for ref in excludes_refs:
            j = index[ref]
            conflicts[i] |= 1 << j
            conflicts[j] |= 1 << i
    conflicts = tuple(conflicts)
    all_mask = (1 << n) - 1

    metrics_cache = {0: (0, (0,) * dimensions)}

    def metrics(mask):
        cached = metrics_cache.get(mask)
        if cached is not None:
            return cached
        total_value = 0
        total_cost = [0] * dimensions
        rest = mask
        while rest:
            bit = rest & -rest
            rest -= bit
            i = bit.bit_length() - 1
            total_value += values[i]
            for d in range(dimensions):
                total_cost[d] += costs[i][d]
        result = (total_value, tuple(total_cost))
        metrics_cache[mask] = result
        return result

    conflict_union_cache = {0: 0}

    def conflict_union(mask):
        cached = conflict_union_cache.get(mask)
        if cached is not None:
            return cached
        result = 0
        rest = mask
        while rest:
            bit = rest & -rest
            rest -= bit
            result |= conflicts[bit.bit_length() - 1]
        conflict_union_cache[mask] = result
        return result

    def has_conflict(mask):
        rest = mask
        while rest:
            bit = rest & -rest
            rest -= bit
            if conflicts[bit.bit_length() - 1] & mask:
                return True
        return False

    # reverse_closures[x] contains every project transitively requiring x.
    reverse_closures = [0] * n
    for i, closure in enumerate(closures):
        rest = closure
        while rest:
            bit = rest & -rest
            rest -= bit
            reverse_closures[bit.bit_length() - 1] |= 1 << i
    reverse_closures = tuple(reverse_closures)
    reverse_union_cache = {0: 0}

    def reverse_union(mask):
        cached = reverse_union_cache.get(mask)
        if cached is not None:
            return cached
        result = 0
        rest = mask
        while rest:
            bit = rest & -rest
            rest -= bit
            result |= reverse_closures[bit.bit_length() - 1]
        reverse_union_cache[mask] = result
        return result

    # Mark projects whose complete closure is intrinsically infeasible.
    invalid = 0
    for i, closure in enumerate(closures):
        _closure_value, closure_cost = metrics(closure)
        if has_conflict(closure) or any(closure_cost[d] > budget[d] for d in range(dimensions)):
            invalid |= 1 << i
    invalid = reverse_union(invalid)

    selected = 0
    for project_id in required:
        selected |= closures[index[project_id]]
    selected_value, selected_cost = metrics(selected)
    if (selected & invalid or has_conflict(selected)
            or any(selected_cost[d] > budget[d] for d in range(dimensions))):
        return None
    forbidden = invalid | reverse_union(conflict_union(selected))
    if selected & forbidden:
        return None

    # Precompute orders for safe fractional-knapsack upper bounds.
    positive_indices = tuple(i for i in range(n) if values[i] > 0)
    weight_vectors = set()
    normalized_weights = []
    for d in range(dimensions):
        weight = 1
        for other in range(dimensions):
            if other != d:
                weight *= budget[other] + 1
        normalized_weights.append(weight)
    for subset in range(1, 1 << dimensions):
        weight_vectors.add(tuple(1 if subset & (1 << d) else 0
                                 for d in range(dimensions)))
        weight_vectors.add(tuple(normalized_weights[d] if subset & (1 << d) else 0
                                 for d in range(dimensions)))

    def ratio_order(aggregate_cost):
        def compare(a, b):
            ca, cb = aggregate_cost[a], aggregate_cost[b]
            if ca == 0 or cb == 0:
                if ca == cb:
                    return -1 if a < b else (1 if a > b else 0)
                return -1 if ca == 0 else 1
            left = values[a] * cb
            right = values[b] * ca
            if left != right:
                return -1 if left > right else 1
            return -1 if a < b else (1 if a > b else 0)
        return tuple(sorted(positive_indices, key=cmp_to_key(compare)))

    fractional_data = []
    for weights in weight_vectors:
        aggregate_cost = tuple(
            sum(weights[d] * costs[i][d] for d in range(dimensions))
            for i in range(n)
        )
        fractional_data.append((weights, aggregate_cost, ratio_order(aggregate_cost)))

    # Coordinate-wise fractional minimum costs support secondary-objective
    # pruning when an upper bound merely ties the incumbent value.
    minimum_cost_orders = []
    for d in range(dimensions):
        def compare_cost(a, b, dimension=d):
            left = costs[a][dimension] * values[b]
            right = costs[b][dimension] * values[a]
            if left != right:
                return -1 if left < right else 1
            return -1 if a < b else (1 if a > b else 0)
        minimum_cost_orders.append(
            tuple(sorted(positive_indices, key=cmp_to_key(compare_cost)))
        )

    # Maximum closure is a dependency-aware upper bound.  Min-cut solves the
    # relaxation exactly; conflicts and budgets are intentionally ignored.
    has_useful_dependencies = any(direct_requirements) and any(v < 0 for v in values)
    closure_bound_cache = {}

    def maximum_closure_gain(mask):
        cached = closure_bound_cache.get(mask)
        if cached is not None:
            return cached
        total_positive = sum(values[i] for i in range(n)
                             if mask & (1 << i) and values[i] > 0)
        if not total_positive:
            closure_bound_cache[mask] = 0
            return 0
        source, sink = n, n + 1
        graph = [[] for _ in range(n + 2)]

        def add_edge(start, end, capacity):
            graph[start].append([end, len(graph[end]), capacity])
            graph[end].append([start, len(graph[start]) - 1, 0])

        absolute_sum = sum(abs(values[i]) for i in range(n) if mask & (1 << i))
        infinite = absolute_sum + 1
        rest = mask
        while rest:
            bit = rest & -rest
            rest -= bit
            i = bit.bit_length() - 1
            if values[i] > 0:
                add_edge(source, i, values[i])
            elif values[i] < 0:
                add_edge(i, sink, -values[i])
            requirements = direct_requirements[i] & mask
            while requirements:
                req_bit = requirements & -requirements
                requirements -= req_bit
                add_edge(i, req_bit.bit_length() - 1, infinite)

        flow = 0
        while True:
            levels = [-1] * (n + 2)
            levels[source] = 0
            queue = deque([source])
            while queue:
                node = queue.popleft()
                for end, _reverse, capacity in graph[node]:
                    if capacity and levels[end] < 0:
                        levels[end] = levels[node] + 1
                        queue.append(end)
            if levels[sink] < 0:
                break
            positions = [0] * (n + 2)

            def push(node, amount):
                if node == sink:
                    return amount
                while positions[node] < len(graph[node]):
                    edge = graph[node][positions[node]]
                    end, reverse, capacity = edge
                    if capacity and levels[end] == levels[node] + 1:
                        sent = push(end, min(amount, capacity))
                        if sent:
                            edge[2] -= sent
                            graph[end][reverse][2] += sent
                            return sent
                    positions[node] += 1
                return 0

            while flow < total_positive:
                sent = push(source, total_positive - flow)
                if not sent:
                    break
                flow += sent
            if flow == total_positive:
                break
        result = total_positive - flow
        closure_bound_cache[mask] = result
        return result

    # A clique cover upper-bounds values in the exclusion graph: a feasible
    # portfolio can take at most one member of each clique.
    has_conflicts = any(conflicts)
    clique_bound_cache = {}

    def clique_cover_gain(mask):
        positive_mask = sum(1 << i for i in positive_indices if mask & (1 << i))
        cached = clique_bound_cache.get(positive_mask)
        if cached is not None:
            return cached
        vertices = [i for i in positive_indices if positive_mask & (1 << i)]
        if not vertices:
            clique_bound_cache[positive_mask] = 0
            return 0
        degree = {i: (conflicts[i] & positive_mask).bit_count() for i in vertices}
        orders = (
            sorted(vertices, key=lambda i: (-degree[i], -values[i], i)),
            sorted(vertices, key=lambda i: (-values[i], -degree[i], i)),
            sorted(vertices),
        )
        best_bound = sum(values[i] for i in vertices)
        for order in orders:
            clique_masks = []
            clique_maxima = []
            for i in order:
                chosen = -1
                saving_best = -1
                for j, clique in enumerate(clique_masks):
                    if clique & ~conflicts[i] == 0:
                        saving = min(values[i], clique_maxima[j])
                        if saving > saving_best:
                            chosen, saving_best = j, saving
                if chosen < 0:
                    clique_masks.append(1 << i)
                    clique_maxima.append(values[i])
                else:
                    clique_masks[chosen] |= 1 << i
                    clique_maxima[chosen] = max(clique_maxima[chosen], values[i])
            best_bound = min(best_bound, sum(clique_maxima))
        clique_bound_cache[positive_mask] = best_bound
        return best_bound

    def mask_ids(mask):
        return tuple(ids[i] for i in range(n) if mask & (1 << i))

    best_mask = selected
    best_value = selected_value
    best_cost = selected_cost
    best_ids = mask_ids(selected)

    def consider(mask, value, cost):
        nonlocal best_mask, best_value, best_cost, best_ids
        if value < best_value or (value == best_value and cost > best_cost):
            return
        candidate_ids = None
        if value == best_value and cost == best_cost:
            candidate_ids = mask_ids(mask)
            if candidate_ids >= best_ids:
                return
        best_mask = mask
        best_value = value
        best_cost = cost
        best_ids = candidate_ids if candidate_ids is not None else mask_ids(mask)

    def solve_independent_mitm(candidates, current_selected,
                               current_value, current_cost,
                               active_dimensions):
        """Solve a residual independent knapsack by meet in the middle.

        This path is especially important for subset-sum-like inputs, where a
        fractional bound is exact at a huge number of branch nodes.  Candidate
        IDs are split contiguously: all left IDs precede all right IDs.  Hence,
        for a fixed selected base, the lexicographic order between two right
        masks is unaffected by which left mask is later combined with them.
        """

        item_indices = [i for i in range(n) if candidates & (1 << i)]
        split = len(item_indices) // 2
        left_items = item_indices[:split]
        right_items = item_indices[split:]
        residual = tuple(budget[d] - current_cost[d]
                         for d in range(dimensions))

        def enumerate_half(items):
            # Entries are (value, cost tuple, original-index mask).
            entries = [(0, (0,) * dimensions, 0)]
            for i in items:
                additions = []
                for value, cost, mask in entries:
                    new_cost = tuple(cost[d] + costs[i][d]
                                     for d in range(dimensions))
                    if all(new_cost[d] <= residual[d]
                           for d in range(dimensions)):
                        additions.append((value + values[i], new_cost,
                                          mask | (1 << i)))
                entries.extend(additions)
            return entries

        left_entries = enumerate_half(left_items)
        right_entries = enumerate_half(right_items)

        def right_better(candidate, incumbent):
            if incumbent is None or candidate[0] != incumbent[0]:
                return incumbent is None or candidate[0] > incumbent[0]
            if candidate[1] != incumbent[1]:
                return candidate[1] < incumbent[1]
            # The fixed selected mask accounts for the otherwise subtle case
            # where one ID tuple is a prefix of another.
            return (mask_ids(current_selected | candidate[2])
                    < mask_ids(current_selected | incumbent[2]))

        def use_pair(left_entry, right_entry):
            left_value, left_cost, left_mask = left_entry
            right_value, right_cost, right_mask = right_entry
            combined_cost = tuple(current_cost[d] + left_cost[d] + right_cost[d]
                                  for d in range(dimensions))
            if any(combined_cost[d] > budget[d] for d in range(dimensions)):
                return
            consider(current_selected | left_mask | right_mask,
                     current_value + left_value + right_value,
                     combined_cost)

        # With no binding dimension, every left/right pair fits and each half
        # can be optimized independently.
        if not active_dimensions:
            right_best = None
            for entry in right_entries:
                if right_better(entry, right_best):
                    right_best = entry
            left_best = None
            for entry in left_entries:
                if left_best is None or entry[0] > left_best[0] or (
                        entry[0] == left_best[0] and entry[1] < left_best[1]) or (
                        entry[0] == left_best[0] and entry[1] == left_best[1]
                        and mask_ids(current_selected | entry[2] | right_best[2])
                        < mask_ids(current_selected | left_best[2] | right_best[2])):
                    left_best = entry
            use_pair(left_best, right_best)
            return

        # One-dimensional prefix maxima.
        if len(active_dimensions) == 1:
            query_dimension = active_dimensions[0]
            right_entries.sort(key=lambda entry: entry[1][query_dimension])
            axes = []
            prefix_best = []
            incumbent = None
            for entry in right_entries:
                axes.append(entry[1][query_dimension])
                if right_better(entry, incumbent):
                    incumbent = entry
                prefix_best.append(incumbent)
            for left_entry in left_entries:
                capacity = residual[query_dimension] - left_entry[1][query_dimension]
                position = bisect_right(axes, capacity) - 1
                if position >= 0:
                    use_pair(left_entry, prefix_best[position])
            return

        # For two dimensions, sweep the first cost and query a Fenwick tree on
        # the second.  Tree nodes retain the best right-half entry.
        first, second = active_dimensions[:2]
        right_entries.sort(key=lambda entry: entry[1][first])
        queries = sorted(
            left_entries,
            key=lambda entry: residual[first] - entry[1][first],
        )
        second_coordinates = sorted({entry[1][second] for entry in right_entries})

        if len(active_dimensions) == 2:
            tree = [None] * (len(second_coordinates) + 1)

            def update(entry):
                position = bisect_left(second_coordinates, entry[1][second]) + 1
                while position < len(tree):
                    if right_better(entry, tree[position]):
                        tree[position] = entry
                    position += position & -position

            def query(capacity):
                position = bisect_right(second_coordinates, capacity)
                result = None
                while position:
                    if tree[position] is not None and right_better(tree[position], result):
                        result = tree[position]
                    position -= position & -position
                return result

            inserted = 0
            for left_entry in queries:
                first_capacity = residual[first] - left_entry[1][first]
                while (inserted < len(right_entries)
                       and right_entries[inserted][1][first] <= first_capacity):
                    update(right_entries[inserted])
                    inserted += 1
                match = query(residual[second] - left_entry[1][second])
                if match is not None:
                    use_pair(left_entry, match)
            return

        # Three-dimensional dominance queries use a Fenwick tree whose nodes
        # are themselves compressed Fenwick trees for the third coordinate.
        third = active_dimensions[2]
        node_coordinates = [[] for _ in range(len(second_coordinates) + 1)]
        for entry in right_entries:
            position = bisect_left(second_coordinates, entry[1][second]) + 1
            while position < len(node_coordinates):
                node_coordinates[position].append(entry[1][third])
                position += position & -position
        node_trees = [None] * len(node_coordinates)
        for position in range(1, len(node_coordinates)):
            node_coordinates[position] = sorted(set(node_coordinates[position]))
            node_trees[position] = [None] * (len(node_coordinates[position]) + 1)

        def update_2d(entry):
            position = bisect_left(second_coordinates, entry[1][second]) + 1
            while position < len(node_coordinates):
                inner = bisect_left(node_coordinates[position], entry[1][third]) + 1
                tree = node_trees[position]
                while inner < len(tree):
                    if right_better(entry, tree[inner]):
                        tree[inner] = entry
                    inner += inner & -inner
                position += position & -position

        def query_2d(second_capacity, third_capacity):
            position = bisect_right(second_coordinates, second_capacity)
            result = None
            while position:
                inner = bisect_right(node_coordinates[position], third_capacity)
                tree = node_trees[position]
                while inner:
                    if tree[inner] is not None and right_better(tree[inner], result):
                        result = tree[inner]
                    inner -= inner & -inner
                position -= position & -position
            return result

        inserted = 0
        for left_entry in queries:
            first_capacity = residual[first] - left_entry[1][first]
            while (inserted < len(right_entries)
                   and right_entries[inserted][1][first] <= first_capacity):
                update_2d(right_entries[inserted])
                inserted += 1
            match = query_2d(
                residual[second] - left_entry[1][second],
                residual[third] - left_entry[1][third],
            )
            if match is not None:
                use_pair(left_entry, match)

    def fractional_upper(mask, current_value, current_cost, initial_upper):
        upper = initial_upper
        for weights, aggregate_cost, order in fractional_data:
            capacity = sum(weights[d] * (budget[d] - current_cost[d])
                           for d in range(dimensions))
            gain = 0
            for i in order:
                if not mask & (1 << i):
                    continue
                item_cost = aggregate_cost[i]
                if item_cost == 0:
                    gain += values[i]
                elif item_cost <= capacity:
                    capacity -= item_cost
                    gain += values[i]
                else:
                    gain += values[i] * capacity // item_cost
                    break
            upper = min(upper, current_value + gain)
            if upper < best_value:
                break
        return upper

    def cost_lower_bound(mask, current_value, current_cost):
        needed = best_value - current_value
        if needed <= 0:
            return current_cost
        result = []
        for d, order in enumerate(minimum_cost_orders):
            remaining_value = needed
            added_cost = 0
            for i in order:
                if not mask & (1 << i):
                    continue
                if values[i] <= remaining_value:
                    added_cost += costs[i][d]
                    remaining_value -= values[i]
                else:
                    numerator = costs[i][d] * remaining_value
                    added_cost += (numerator + values[i] - 1) // values[i]
                    remaining_value = 0
                if remaining_value == 0:
                    break
            if remaining_value:
                return tuple(budget[x] + 1 for x in range(dimensions))
            result.append(current_cost[d] + added_cost)
        return tuple(result)

    branch_weights = tuple(normalized_weights)

    def choose_project(mask, current_selected):
        chosen = -1
        chosen_numerator = 0
        chosen_denominator = 1
        chosen_impact = -1
        rest = mask
        while rest:
            bit = rest & -rest
            rest -= bit
            i = bit.bit_length() - 1
            added = closures[i] & ~current_selected
            delta_value, delta_cost = metrics(added)
            numerator = max(0, values[i], delta_value)
            denominator = sum(branch_weights[d] * delta_cost[d]
                              for d in range(dimensions))
            impact = (added.bit_count()
                      + (reverse_closures[i] & mask).bit_count()
                      + (conflicts[i] & mask).bit_count())
            if chosen < 0:
                better = True
            elif numerator and chosen_numerator:
                if denominator == 0 or chosen_denominator == 0:
                    better = denominator == 0 and chosen_denominator != 0
                else:
                    left = numerator * chosen_denominator
                    right = chosen_numerator * denominator
                    better = left > right
                    if left == right:
                        better = impact > chosen_impact or (impact == chosen_impact and i < chosen)
            elif numerator != chosen_numerator:
                better = numerator > chosen_numerator
            else:
                better = impact > chosen_impact or (impact == chosen_impact and i < chosen)
            if better:
                chosen = i
                chosen_numerator = numerator
                chosen_denominator = denominator
                chosen_impact = impact
        return chosen

    seen = set()

    def search(current_selected, current_forbidden, current_value, current_cost):
        candidates = all_mask & ~current_selected & ~current_forbidden

        # A closure that does not fit now cannot fit after adding nonnegative
        # costs.  Rule it and all reverse dependents out immediately.
        over_budget = 0
        rest = candidates
        while rest:
            bit = rest & -rest
            rest -= bit
            i = bit.bit_length() - 1
            _delta_value, delta_cost = metrics(closures[i] & ~current_selected)
            if any(current_cost[d] + delta_cost[d] > budget[d]
                   for d in range(dimensions)):
                over_budget |= bit
        if over_budget:
            current_forbidden |= reverse_union(over_budget)
            candidates = all_mask & ~current_selected & ~current_forbidden

        state = (current_selected, current_forbidden)
        if state in seen:
            return
        seen.add(state)

        # Excluding every undecided item is always a feasible completion.
        consider(current_selected, current_value, current_cost)
        if not candidates:
            return

        # When all remaining choices are independent, an exact meet-in-the-
        # middle solve with one-, two-, or three-dimensional dominance queries
        # avoids the classic subset-sum weakness of branch-and-bound.  Smaller
        # residuals are cheaper to branch directly.
        if candidates.bit_count() >= 18:
            independent = (conflict_union(candidates) & candidates) == 0
            if independent:
                rest = candidates
                while rest:
                    bit = rest & -rest
                    rest -= bit
                    i = bit.bit_length() - 1
                    if closures[i] & candidates != bit:
                        independent = False
                        break
            if independent:
                _candidate_value, candidate_cost = metrics(candidates)
                active_dimensions = [
                    d for d in range(dimensions)
                    if candidate_cost[d] > budget[d] - current_cost[d]
                ]
                if len(active_dimensions) <= 3:
                    solve_independent_mitm(
                        candidates, current_selected, current_value,
                        current_cost, active_dimensions,
                    )
                    return

        positive_sum = sum(values[i] for i in positive_indices
                           if candidates & (1 << i))
        upper = current_value + positive_sum
        if upper < best_value:
            return
        upper = fractional_upper(candidates, current_value, current_cost, upper)
        if upper < best_value:
            return
        if has_useful_dependencies:
            upper = min(upper, current_value + maximum_closure_gain(candidates))
            if upper < best_value:
                return
        if has_conflicts:
            upper = min(upper, current_value + clique_cover_gain(candidates))
            if upper < best_value:
                return

        if upper == best_value:
            lower_cost = cost_lower_bound(candidates, current_value, current_cost)
            if lower_cost > best_cost:
                return
            if lower_cost == best_cost:
                if current_selected:
                    highest = current_selected.bit_length() - 1
                    lex_lower_mask = (current_selected
                                      | (candidates & ((1 << highest) - 1)))
                else:
                    lex_lower_mask = 0
                if best_ids <= mask_ids(lex_lower_mask):
                    return

        i = choose_project(candidates, current_selected)
        added = closures[i] & ~current_selected
        delta_value, delta_cost = metrics(added)
        included_selected = current_selected | added
        included_forbidden = (current_forbidden
                              | reverse_union(conflict_union(added)))
        included_cost = tuple(current_cost[d] + delta_cost[d]
                              for d in range(dimensions))

        def include_branch():
            if not included_selected & included_forbidden:
                search(included_selected, included_forbidden,
                       current_value + delta_value, included_cost)

        def exclude_branch():
            search(current_selected,
                   current_forbidden | reverse_closures[i],
                   current_value, current_cost)

        # Ordering obtains a strong incumbent; only bounds establish pruning.
        lex_helpful_zero = (delta_value == 0 and current_selected
                            and i < current_selected.bit_length() - 1)
        if delta_value > 0 or values[i] > 0 or lex_helpful_zero:
            include_branch()
            exclude_branch()
        else:
            exclude_branch()
            include_branch()

    search(selected, forbidden, selected_value, selected_cost)
    return {"selected": list(best_ids), "value": best_value, "cost": list(best_cost)}
