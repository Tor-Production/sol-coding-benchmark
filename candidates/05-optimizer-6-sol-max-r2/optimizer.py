"""Exact optimization of small portfolios with prerequisites and exclusions."""

from bisect import bisect_left, bisect_right
from functools import cmp_to_key

_KEYS = {"id", "value", "cost", "requires", "excludes"}


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _bits(mask):
    while mask:
        bit = mask & -mask
        yield bit.bit_length() - 1
        mask -= bit


def _validate(projects, budget, required):
    if not isinstance(projects, list) or len(projects) > 32:
        raise ValueError("projects must be a list of at most 32 dictionaries")
    if (not isinstance(budget, list) or not 1 <= len(budget) <= 3
            or any(not _integer(x) or x < 0 for x in budget)):
        raise ValueError("budget must be a list of 1 to 3 nonnegative integers")
    if not isinstance(required, (list, tuple)):
        raise ValueError("required must be a list or tuple")
    by_id = {}
    for project in projects:
        if not isinstance(project, dict) or set(project) != _KEYS:
            raise ValueError("each project must have exactly the specified keys")
        name = project["id"]
        if not isinstance(name, str) or not name or name in by_id:
            raise ValueError("project IDs must be unique nonempty strings")
        if not _integer(project["value"]):
            raise ValueError("project value must be an integer")
        cost = project["cost"]
        if (not isinstance(cost, list) or len(cost) != len(budget)
                or any(not _integer(x) or x < 0 for x in cost)):
            raise ValueError("project cost has invalid dimensions or entries")
        for key in ("requires", "excludes"):
            refs = project[key]
            if not isinstance(refs, list) or any(not isinstance(x, str) for x in refs):
                raise ValueError("references must be lists of IDs")
            if len(refs) != len(set(refs)):
                raise ValueError("duplicate project reference")
        by_id[name] = project
    names = sorted(by_id)
    index = {name: i for i, name in enumerate(names)}
    if (any(not isinstance(x, str) or x not in index for x in required)
            or len(required) != len(set(required))):
        raise ValueError("required IDs must be distinct and known")
    ordered = [by_id[name] for name in names]
    needs = []
    conflicts = [0] * len(names)
    for i, project in enumerate(ordered):
        req = 0
        for name in project["requires"]:
            if name not in index or index[name] == i:
                raise ValueError("invalid prerequisite")
            req |= 1 << index[name]
        needs.append(req)
        for name in project["excludes"]:
            if name not in index or index[name] == i:
                raise ValueError("invalid exclusion")
            j = index[name]
            conflicts[i] |= 1 << j
            conflicts[j] |= 1 << i
    closure = [0] * len(names)
    visiting = [0] * len(names)

    def visit(i):
        if visiting[i] == 1:
            raise ValueError("dependency cycle")
        if visiting[i] == 2:
            return closure[i]
        visiting[i] = 1
        result = 1 << i
        for j in _bits(needs[i]):
            result |= visit(j)
        closure[i] = result
        visiting[i] = 2
        return result

    for i in range(len(names)):
        visit(i)
    required_mask = 0
    for name in required:
        required_mask |= closure[index[name]]
    values = [project["value"] for project in ordered]
    costs = [tuple(project["cost"]) for project in ordered]
    return names, values, costs, needs, conflicts, closure, required_mask


def _totals(mask, values, costs, dimensions):
    value = 0
    total = [0] * dimensions
    for i in _bits(mask):
        value += values[i]
        for d in range(dimensions):
            total[d] += costs[i][d]
    return value, tuple(total)


def _conflicting(mask, conflicts):
    return any(conflicts[i] & mask for i in _bits(mask))


def _components(active, needs, conflicts):
    adjacency = [needs[i] | conflicts[i] for i in range(len(needs))]
    for i in range(len(needs)):
        for j in _bits(needs[i]):
            adjacency[j] |= 1 << i
    unseen = active
    result = []
    while unseen:
        bit = unseen & -unseen
        unseen -= bit
        group = bit
        frontier = bit
        while frontier:
            neighbors = 0
            for i in _bits(frontier):
                neighbors |= adjacency[i] & unseen
            frontier = neighbors
            group |= neighbors
            unseen &= ~neighbors
        result.append(tuple(_bits(group)))
    return result


def _component_options(component, closure, conflicts, values, costs,
                       remaining_budget):
    """Enumerate feasible subsets of one disconnected component."""
    size = len(component)
    count = 1 << size
    local_index = {i: j for j, i in enumerate(component)}
    local_needs = []
    local_conflicts = []
    for i in component:
        local_needs.append(sum(1 << local_index[j] for j in _bits(closure[i])
                               if j in local_index))
        local_conflicts.append(sum(1 << local_index[j] for j in _bits(conflicts[i])
                                   if j in local_index))
    dimensions = len(remaining_budget)
    unions = [0] * count
    bad = [False] * count
    sums = [0] * count
    totals = [(0,) * dimensions] * count
    global_masks = [0] * count
    options = [(0, (0,) * dimensions, 0)]
    for mask in range(1, count):
        bit = mask & -mask
        j = bit.bit_length() - 1
        prev = mask - bit
        unions[mask] = unions[prev] | local_needs[j]
        bad[mask] = bad[prev] or bool(local_conflicts[j] & prev)
        sums[mask] = sums[prev] + values[component[j]]
        totals[mask] = tuple(totals[prev][d] + costs[component[j]][d]
                             for d in range(dimensions))
        global_masks[mask] = global_masks[prev] | (1 << component[j])
        if (not bad[mask] and unions[mask] == mask
                and all(totals[mask][d] <= remaining_budget[d]
                        for d in range(dimensions))):
            options.append((sums[mask], totals[mask], global_masks[mask]))
    return options


def _make_states(groups, budget):
    dimensions = len(budget)
    states = [(0, (0,) * dimensions, 0)]
    for options in groups:
        next_states = []
        for value, cost, mask in states:
            for extra_value, extra_cost, extra_mask in options:
                new_cost = tuple(cost[d] + extra_cost[d]
                                 for d in range(dimensions))
                if all(new_cost[d] <= budget[d] for d in range(dimensions)):
                    next_states.append((value + extra_value, new_cost,
                                        mask | extra_mask))
        states = next_states
    return states


def _meet_in_middle(left_groups, right_groups, budget, names, base_mask,
                    base_value, base_cost):
    """Combine independent halves through offline dominance maximum queries."""
    dimensions = len(budget)
    remaining_budget = tuple(budget[d] - base_cost[d]
                             for d in range(dimensions))
    left = _make_states(left_groups, remaining_budget)
    right = _make_states(right_groups, remaining_budget)

    def selected(mask):
        return tuple(names[i] for i in _bits(mask))

    right_names = [selected(base_mask | record[2]) for record in right]
    ranks = [0] * len(right)
    for rank, index in enumerate(sorted(range(len(right)),
                                        key=right_names.__getitem__)):
        ranks[index] = rank
    scores = [(record[0], *(-x for x in record[1]), -ranks[i])
              for i, record in enumerate(right)]
    right_order = sorted(range(len(right)), key=lambda i: right[i][1][0])
    left_order = sorted(left, key=lambda record:
                        remaining_budget[0] - record[1][0])

    if dimensions == 1:
        best_right = -1

        def insert(i):
            nonlocal best_right
            if best_right < 0 or scores[i] > scores[best_right]:
                best_right = i

        def query(_cost):
            return best_right

    elif dimensions == 2:
        coordinates = sorted({record[1][1] for record in right})
        tree = [-1] * (len(coordinates) + 1)

        def insert(i):
            pos = bisect_left(coordinates, right[i][1][1]) + 1
            while pos < len(tree):
                old = tree[pos]
                if old < 0 or scores[i] > scores[old]:
                    tree[pos] = i
                pos += pos & -pos

        def query(cost):
            pos = bisect_right(coordinates, cost[1])
            result = -1
            while pos:
                candidate = tree[pos]
                if candidate >= 0 and (result < 0
                                       or scores[candidate] > scores[result]):
                    result = candidate
                pos -= pos & -pos
            return result

    else:
        coordinates = sorted({record[1][1] for record in right})
        outer_size = len(coordinates)
        inner_coordinates = [[] for _ in range(outer_size + 1)]
        for record in right:
            pos = bisect_left(coordinates, record[1][1]) + 1
            while pos <= outer_size:
                inner_coordinates[pos].append(record[1][2])
                pos += pos & -pos
        inner_coordinates = [sorted(set(row)) for row in inner_coordinates]
        tree = [[-1] * (len(row) + 1) for row in inner_coordinates]

        def insert(i):
            pos = bisect_left(coordinates, right[i][1][1]) + 1
            second = right[i][1][2]
            while pos <= outer_size:
                row = tree[pos]
                inner = bisect_left(inner_coordinates[pos], second) + 1
                while inner < len(row):
                    old = row[inner]
                    if old < 0 or scores[i] > scores[old]:
                        row[inner] = i
                    inner += inner & -inner
                pos += pos & -pos

        def query(cost):
            pos = bisect_right(coordinates, cost[1])
            result = -1
            while pos:
                row = tree[pos]
                inner = bisect_right(inner_coordinates[pos], cost[2])
                while inner:
                    candidate = row[inner]
                    if candidate >= 0 and (result < 0
                                           or scores[candidate] > scores[result]):
                        result = candidate
                    inner -= inner & -inner
                pos -= pos & -pos
            return result

    answer_value = base_value
    answer_cost = base_cost
    answer_mask = base_mask
    answer_names = selected(base_mask)
    pointer = 0
    for left_value, left_cost, left_mask in left_order:
        limit = remaining_budget[0] - left_cost[0]
        while pointer < len(right_order) and right[right_order[pointer]][1][0] <= limit:
            insert(right_order[pointer])
            pointer += 1
        room = tuple(remaining_budget[d] - left_cost[d]
                     for d in range(dimensions))
        j = query(room)
        if j < 0:
            continue
        right_value, right_cost, right_mask = right[j]
        value = base_value + left_value + right_value
        if value < answer_value:
            continue
        total_cost = tuple(base_cost[d] + left_cost[d] + right_cost[d]
                           for d in range(dimensions))
        if value == answer_value and total_cost > answer_cost:
            continue
        mask = base_mask | left_mask | right_mask
        if value == answer_value and total_cost == answer_cost:
            chosen_names = selected(mask)
            if chosen_names >= answer_names:
                continue
        else:
            chosen_names = selected(mask)
        answer_value, answer_cost = value, total_cost
        answer_mask, answer_names = mask, chosen_names
    return answer_mask, answer_value, answer_cost


def _branch(active, budget, names, values, costs, conflicts, closure,
            base_mask, base_value, base_cost):
    """Exact include/exclude search with propagation and fractional bounds."""
    dimensions = len(budget)
    n = len(names)
    dependent = [0] * n
    for i in _bits(active):
        for j in _bits(closure[i] & active):
            dependent[j] |= 1 << i
    active_closure = [mask & active for mask in closure]
    positive = [i for i in _bits(active) if values[i] > 0]

    def ratio_compare(d):
        def compare(i, j):
            a, b = costs[i][d], costs[j][d]
            if a == 0 or b == 0:
                if a == b:
                    return 0
                return -1 if a == 0 else 1
            difference = values[i] * b - values[j] * a
            return -1 if difference > 0 else (1 if difference < 0 else 0)
        return compare

    orders = [sorted(positive, key=cmp_to_key(ratio_compare(d)))
              for d in range(dimensions)]
    answer_mask = base_mask
    answer_value = base_value
    answer_cost = base_cost

    def selected(mask):
        return tuple(names[i] for i in _bits(mask))

    answer_names = selected(base_mask)

    def forbidden_dependents(mask):
        result = 0
        for i in _bits(mask & active):
            result |= dependent[i]
        return result

    def search(chosen, forbidden, value, cost):
        nonlocal answer_mask, answer_value, answer_cost, answer_names
        full_mask = base_mask | chosen
        if (value > answer_value or
                (value == answer_value and cost <= answer_cost)):
            candidate_names = selected(full_mask)
            if (value > answer_value or cost < answer_cost
                    or candidate_names < answer_names):
                answer_mask, answer_value, answer_cost = full_mask, value, cost
                answer_names = candidate_names
        remaining = active & ~(chosen | forbidden)
        if not remaining:
            return
        available = list(_bits(remaining))
        upper = value + sum(values[i] for i in available if values[i] > 0)
        if upper < answer_value:
            return
        if upper == answer_value and cost > answer_cost:
            return
        for d in range(dimensions):
            capacity = budget[d] - cost[d]
            bound = value
            for i in orders[d]:
                if not (remaining >> i) & 1:
                    continue
                amount = costs[i][d]
                if amount <= capacity:
                    bound += values[i]
                    capacity -= amount
                else:
                    bound += values[i] * capacity // amount
                    break
            if bound < answer_value:
                return
            if bound == answer_value and cost > answer_cost:
                return

        def impact(i):
            return ((conflicts[i] | dependent[i] | active_closure[i])
                    & remaining).bit_count(), abs(values[i])

        i = max(available, key=impact)
        added = active_closure[i] & ~chosen
        possible = not (added & forbidden)
        if possible:
            new_cost = tuple(cost[d] + sum(costs[j][d] for j in _bits(added))
                             for d in range(dimensions))
            possible = all(new_cost[d] <= budget[d] for d in range(dimensions))
        if possible:
            new_value = value + sum(values[j] for j in _bits(added))
            new_conflicts = 0
            for j in _bits(added):
                new_conflicts |= conflicts[j]
            include_forbidden = forbidden | forbidden_dependents(new_conflicts)
            if new_value >= value:
                search(chosen | added, include_forbidden, new_value, new_cost)
                search(chosen, forbidden | dependent[i], value, cost)
            else:
                search(chosen, forbidden | dependent[i], value, cost)
                search(chosen | added, include_forbidden, new_value, new_cost)
        else:
            search(chosen, forbidden | dependent[i], value, cost)

    search(0, 0, base_value, base_cost)
    return answer_mask, answer_value, answer_cost


def _product(numbers):
    result = 1
    for number in numbers:
        result *= number
    return result


def solve(projects, budget, required=()):
    """Return the globally best feasible portfolio, or None if impossible."""
    (names, values, costs, needs, conflicts, closure,
     base_mask) = _validate(projects, budget, required)
    dimensions = len(budget)
    if _conflicting(base_mask, conflicts):
        return None
    base_value, base_cost = _totals(base_mask, values, costs, dimensions)
    if any(base_cost[d] > budget[d] for d in range(dimensions)):
        return None
    active = 0
    for i in range(len(names)):
        bit = 1 << i
        if base_mask & bit:
            continue
        trial = base_mask | closure[i]
        if _conflicting(trial, conflicts):
            continue
        _, trial_cost = _totals(trial, values, costs, dimensions)
        if all(trial_cost[d] <= budget[d] for d in range(dimensions)):
            active |= bit
    if all(values[i] <= 0 for i in _bits(active)):
        # The forced portfolio already has the maximum value and minimum
        # cost. Only free, zero-value additions can improve its ID tuple.
        chosen = base_mask
        chosen_names = tuple(names[i] for i in _bits(chosen))
        for i in _bits(active):
            trial = chosen | closure[i]
            added = trial & ~chosen
            if (any(values[j] != 0 or any(costs[j]) for j in _bits(added))
                    or _conflicting(trial, conflicts)):
                continue
            trial_names = tuple(names[j] for j in _bits(trial))
            if trial_names < chosen_names:
                chosen, chosen_names = trial, trial_names
        return {"selected": list(chosen_names), "value": base_value,
                "cost": list(base_cost)}
    components = _components(active, needs, conflicts)
    independent = all(len(component) == 1 for component in components)
    safe_ties = independent or all(
        any(costs[i]) or values[i] > 0 for i in _bits(active))
    answer = None
    if safe_ties and all(len(component) <= 16 for component in components):
        remaining_budget = tuple(budget[d] - base_cost[d]
                                 for d in range(dimensions))
        groups = [_component_options(component, closure, conflicts,
                                     values, costs, remaining_budget)
                  for component in components]
        if independent:
            midpoint = len(groups) // 2
            left_groups, right_groups = groups[:midpoint], groups[midpoint:]
        else:
            ordering = sorted(range(len(groups)),
                              key=lambda j: len(groups[j]), reverse=True)
            sides = [[], []]
            sizes = [1, 1]
            for j in ordering:
                side = 0 if sizes[0] <= sizes[1] else 1
                sides[side].append(groups[j])
                sizes[side] *= len(groups[j])
            left_groups, right_groups = sides
        if independent or (max(
                _product(len(group) for group in side)
                for side in (left_groups, right_groups)) <= 120000):
            answer = _meet_in_middle(left_groups, right_groups, budget,
                                     names, base_mask, base_value, base_cost)
    if answer is None:
        answer = _branch(active, budget, names, values, costs, conflicts,
                         closure, base_mask, base_value, base_cost)
    mask, value, cost = answer
    return {"selected": [names[i] for i in _bits(mask)],
            "value": value, "cost": list(cost)}
