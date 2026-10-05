"""Exact constrained portfolio optimization.

The implementation deliberately uses bit masks: the public limit is only 32
projects, and masks make dependency and conflict propagation inexpensive.
"""

from fractions import Fraction
from functools import cmp_to_key


_PROJECT_KEYS = {"id", "value", "cost", "requires", "excludes"}


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _validate(projects, budget, required):
    if not isinstance(projects, list) or len(projects) > 32:
        raise ValueError("projects must be a list containing at most 32 items")
    if (not isinstance(budget, list) or not 1 <= len(budget) <= 3 or
            any(not _integer(x) or x < 0 for x in budget)):
        raise ValueError("budget must contain one to three nonnegative integers")
    if not isinstance(required, (list, tuple)):
        raise ValueError("required must be a list or tuple")

    dimension = len(budget)
    ids = []
    seen_ids = set()
    for project in projects:
        if not isinstance(project, dict) or set(project) != _PROJECT_KEYS:
            raise ValueError("each project must have exactly the documented keys")
        project_id = project["id"]
        if not isinstance(project_id, str) or not project_id or project_id in seen_ids:
            raise ValueError("project IDs must be unique nonempty strings")
        seen_ids.add(project_id)
        ids.append(project_id)
        if not _integer(project["value"]):
            raise ValueError("project values must be integers")
        cost = project["cost"]
        if (not isinstance(cost, list) or len(cost) != dimension or
                any(not _integer(x) or x < 0 for x in cost)):
            raise ValueError("project costs must match the budget dimensions")
        for field in ("requires", "excludes"):
            refs = project[field]
            if not isinstance(refs, list):
                raise ValueError("requires and excludes must be lists")
            if any(not isinstance(x, str) or not x for x in refs):
                raise ValueError("references must be nonempty strings")
            if len(set(refs)) != len(refs):
                raise ValueError("references in a list must be distinct")

    index = {project_id: i for i, project_id in enumerate(ids)}
    for project in projects:
        project_id = project["id"]
        for field in ("requires", "excludes"):
            for ref in project[field]:
                if ref not in index or ref == project_id:
                    raise ValueError("references must name another known project")

    if any(not isinstance(x, str) or not x for x in required):
        raise ValueError("required IDs must be nonempty strings")
    if len(set(required)) != len(required) or any(x not in index for x in required):
        raise ValueError("required IDs must be distinct and known")

    state = [0] * len(projects)
    order = []

    def visit(i):
        if state[i] == 1:
            raise ValueError("the dependency graph must be acyclic")
        if state[i] == 2:
            return
        state[i] = 1
        for ref in projects[i]["requires"]:
            visit(index[ref])
        state[i] = 2
        order.append(i)

    for i in range(len(projects)):
        visit(i)
    return ids, index, order


def solve(projects, budget, required=()):
    """Return the globally optimal feasible portfolio, or ``None``."""
    ids, index, topo = _validate(projects, budget, required)
    n = len(projects)
    dimensions = len(budget)
    if not n:
        return {"selected": [], "value": 0, "cost": [0] * dimensions}

    values = tuple(project["value"] for project in projects)
    costs = tuple(tuple(project["cost"]) for project in projects)
    all_mask = (1 << n) - 1

    direct_require = [0] * n
    exclusion = [0] * n
    for i, project in enumerate(projects):
        for ref in project["requires"]:
            direct_require[i] |= 1 << index[ref]
        for ref in project["excludes"]:
            j = index[ref]
            exclusion[i] |= 1 << j
            exclusion[j] |= 1 << i

    closure = [0] * n
    for i in topo:
        mask = 1 << i
        refs = direct_require[i]
        while refs:
            bit = refs & -refs
            refs -= bit
            mask |= closure[bit.bit_length() - 1]
        closure[i] = mask

    reverse = [1 << i for i in range(n)]
    for dependant in range(n):
        mask = closure[dependant]
        while mask:
            bit = mask & -mask
            mask -= bit
            reverse[bit.bit_length() - 1] |= 1 << dependant

    # Dense caches are useful for small instances but would be inappropriate
    # at the 32-project limit.
    mask_value = [None] * (1 << n) if n <= 20 else None
    mask_cost = [None] * (1 << n) if n <= 20 else None

    def totals(mask):
        if mask_value is not None and mask_value[mask] is not None:
            return mask_value[mask], mask_cost[mask]
        value = 0
        total = [0] * dimensions
        work = mask
        while work:
            bit = work & -work
            work -= bit
            i = bit.bit_length() - 1
            value += values[i]
            for d in range(dimensions):
                total[d] += costs[i][d]
        result = tuple(total)
        if mask_value is not None:
            mask_value[mask] = value
            mask_cost[mask] = result
        return value, result

    def forbidden_dependants(mask):
        result = 0
        while mask:
            bit = mask & -mask
            mask -= bit
            result |= reverse[bit.bit_length() - 1]
        return result

    impossible = 0
    for i in range(n):
        cmask = closure[i]
        conflict = False
        work = cmask
        while work and not conflict:
            bit = work & -work
            work -= bit
            j = bit.bit_length() - 1
            conflict = bool(exclusion[j] & cmask)
        _, ccost = totals(cmask)
        if conflict or any(ccost[d] > budget[d] for d in range(dimensions)):
            impossible |= 1 << i
    initial_banned = forbidden_dependants(impossible)

    required_mask = 0
    for project_id in required:
        required_mask |= closure[index[project_id]]
    if required_mask & initial_banned:
        return None

    def exclusion_union(mask):
        result = 0
        while mask:
            bit = mask & -mask
            mask -= bit
            result |= exclusion[bit.bit_length() - 1]
        return result

    required_conflicts = exclusion_union(required_mask)
    if required_conflicts & required_mask:
        return None
    initial_banned |= forbidden_dependants(required_conflicts)
    if initial_banned & required_mask:
        return None
    initial_value, initial_cost = totals(required_mask)
    if any(initial_cost[d] > budget[d] for d in range(dimensions)):
        return None

    sorted_indices = tuple(sorted(range(n), key=lambda i: ids[i]))
    id_rank = {i: rank for rank, i in enumerate(sorted_indices)}
    tuple_cache = {}

    def id_tuple(mask):
        cached = tuple_cache.get(mask)
        if cached is not None:
            return cached
        answer = tuple(ids[i] for i in sorted_indices if mask & (1 << i))
        if len(tuple_cache) < 100000:
            tuple_cache[mask] = answer
        return answer

    best_mask = required_mask
    best_value = initial_value
    best_cost = initial_cost
    best_ids = id_tuple(required_mask)

    def better(value, cost, mask):
        nonlocal best_mask, best_value, best_cost, best_ids
        candidate_ids = None
        if value > best_value or (value == best_value and cost < best_cost):
            pass
        elif value == best_value and cost == best_cost:
            candidate_ids = id_tuple(mask)
            if candidate_ids >= best_ids:
                return False
        else:
            return False
        if candidate_ids is None:
            candidate_ids = id_tuple(mask)
        best_mask, best_value, best_cost, best_ids = mask, value, cost, candidate_ids
        return True

    def include(selected, banned, value, cost, i):
        add = closure[i] & ~selected
        if add & banned:
            return None
        add_value, add_cost = totals(add)
        new_cost = tuple(cost[d] + add_cost[d] for d in range(dimensions))
        if any(new_cost[d] > budget[d] for d in range(dimensions)):
            return None
        new_selected = selected | add
        newly_forbidden = exclusion_union(add)
        if newly_forbidden & new_selected:
            return None
        new_banned = banned | forbidden_dependants(newly_forbidden)
        if new_banned & new_selected:
            return None
        return new_selected, new_banned, value + add_value, new_cost

    # Exact ratio orderings for fractional upper and lower bounds.
    def ratio_cmp(d, descending):
        def compare(a, b):
            ca, cb = costs[a][d], costs[b][d]
            va, vb = values[a], values[b]
            if ca == 0 and cb == 0:
                result = 0
            elif ca == 0:
                result = -1
            elif cb == 0:
                result = 1
            else:
                left, right = va * cb, vb * ca
                result = -1 if left > right else (1 if left < right else 0)
            if not descending:
                result = -result
            if result:
                return result
            return -1 if id_rank[a] < id_rank[b] else (1 if id_rank[a] > id_rank[b] else 0)
        return compare

    positive = tuple(i for i in range(n) if values[i] > 0)
    value_per_cost = []
    cost_per_value = []
    for d in range(dimensions):
        value_per_cost.append(tuple(sorted(positive, key=cmp_to_key(ratio_cmp(d, True)))))
        # Minimizing cost per unit value is the same order as maximizing
        # value per unit cost (with zero-cost items first).
        cost_per_value.append(tuple(sorted(positive, key=cmp_to_key(ratio_cmp(d, True)))))
    value_desc = tuple(sorted(positive, key=lambda i: (-values[i], id_rank[i])))

    def exclusion_clique_bound(value, undecided):
        vertices = [i for i in positive if undecided & (1 << i)]
        vertices.sort(key=lambda i: (
            -(exclusion[i] & undecided).bit_count(), -values[i], id_rank[i]))
        cliques = []
        maxima = []
        for i in vertices:
            eligible = []
            for number, clique in enumerate(cliques):
                if all(exclusion[i] & (1 << j) for j in clique):
                    increase = max(0, values[i] - maxima[number])
                    eligible.append((increase, number))
            if eligible:
                _, number = min(eligible)
                cliques[number].append(i)
                maxima[number] = max(maxima[number], values[i])
            else:
                cliques.append([i])
                maxima.append(values[i])
        return value + sum(maxima), cliques, maxima

    def upper_bound(value, cost, undecided):
        raw = value
        work = undecided
        while work:
            bit = work & -work
            work -= bit
            v = values[bit.bit_length() - 1]
            if v > 0:
                raw += v
        clique_upper, cliques, maxima = exclusion_clique_bound(value, undecided)
        upper = min(raw, clique_upper)
        for d in range(dimensions):
            capacity = budget[d] - cost[d]
            bound = value
            for i in value_per_cost[d]:
                if not (undecided & (1 << i)):
                    continue
                c = costs[i][d]
                if c == 0:
                    bound += values[i]
                elif c <= capacity:
                    bound += values[i]
                    capacity -= c
                else:
                    bound += (values[i] * capacity) // c
                    break
            upper = min(upper, bound)
        return upper, clique_upper, cliques, maxima

    def cost_lower_bound(gap, undecided, d):
        if gap <= 0:
            return 0
        remaining = gap
        result = 0
        for i in cost_per_value[d]:
            if not (undecided & (1 << i)):
                continue
            v, c = values[i], costs[i][d]
            if v <= remaining:
                result += c
                remaining -= v
            else:
                result += (c * remaining + v - 1) // v
                remaining = 0
            if remaining == 0:
                return result
        return 10 ** 100

    def minimum_additions(gap, undecided):
        if gap <= 0:
            return 0
        count = gained = 0
        for i in value_desc:
            if undecided & (1 << i):
                count += 1
                gained += values[i]
                if gained >= gap:
                    return count
        return n + 1

    def optimistic_ids(selected, undecided, at_least):
        chosen = [i for i in sorted_indices if selected & (1 << i)]
        available = [i for i in sorted_indices if undecided & (1 << i)]
        if not chosen:
            take = available[:at_least]
        else:
            last_rank = id_rank[chosen[-1]]
            before = [i for i in available if id_rank[i] < last_rank]
            needed = max(0, at_least - len(before))
            after = [i for i in available if id_rank[i] > last_rank]
            take = before + after[:needed]
        mask = selected
        for i in take:
            mask |= 1 << i
        return id_tuple(mask)

    def clique_optimistic_ids(selected, undecided, cliques, maxima):
        # With no dependency edges, attaining a clique-cover bound requires a
        # maximum-value member of every clique. Choosing the smallest such ID
        # gives a relaxation of the lexicographically smallest completion.
        mask = selected
        for clique, maximum in zip(cliques, maxima):
            choices = [i for i in clique if values[i] == maximum]
            mask |= 1 << min(choices, key=lambda i: id_rank[i])
        if mask:
            last_rank = max(id_rank[i] for i in range(n) if mask & (1 << i))
            for i in sorted_indices:
                if (undecided & (1 << i) and values[i] == 0 and
                        id_rank[i] < last_rank):
                    mask |= 1 << i
        return id_tuple(mask)

    # Seed branch-and-bound with a deterministic feasible greedy solution.
    greedy = (required_mask, initial_banned, initial_value, initial_cost)
    while True:
        selected, banned, value, cost = greedy
        candidate = candidate_key = None
        work = all_mask & ~(selected | banned)
        while work:
            bit = work & -work
            work -= bit
            i = bit.bit_length() - 1
            trial = include(selected, banned, value, cost, i)
            if trial is None:
                continue
            gain = trial[2] - value
            if gain <= 0:
                continue
            extra = sum(trial[3][d] - cost[d] for d in range(dimensions))
            key = (Fraction(gain, extra or 1), gain, -id_rank[i])
            if candidate_key is None or key > candidate_key:
                candidate_key, candidate = key, trial
        if candidate is None:
            break
        greedy = candidate
        better(greedy[2], greedy[3], greedy[0])

    visited = set()

    def search(selected, banned, value, cost):
        state_key = (selected, banned)
        if state_key in visited:
            return
        visited.add(state_key)
        better(value, cost, selected)
        undecided = all_mask & ~(selected | banned)
        if not undecided:
            return
        upper, clique_upper, cliques, maxima = upper_bound(value, cost, undecided)
        if upper < best_value:
            return
        if upper == best_value:
            gap = best_value - value
            lower_cost = tuple(cost[d] + cost_lower_bound(gap, undecided, d)
                               for d in range(dimensions))
            if lower_cost > best_cost:
                return
            if lower_cost == best_cost:
                additions = minimum_additions(gap, undecided)
                optimistic = optimistic_ids(selected, undecided, additions)
                if clique_upper == best_value and not any(direct_require):
                    optimistic = clique_optimistic_ids(
                        selected, undecided, cliques, maxima)
                if optimistic >= best_ids:
                    return

        chosen_i = chosen_key = None
        work = undecided
        while work:
            bit = work & -work
            work -= bit
            i = bit.bit_length() - 1
            forced_in = (closure[i] & ~selected).bit_count()
            forced_out = (reverse[i] & undecided).bit_count()
            conflict_out = (forbidden_dependants(exclusion_union(closure[i])) & undecided).bit_count()
            impact = forced_in + forced_out + conflict_out
            denom = 1 + sum(costs[j][d] for j in range(n)
                            if closure[i] & (1 << j) for d in range(dimensions))
            gain, _ = totals(closure[i] & ~selected)
            key = (impact, Fraction(gain, denom), -id_rank[i])
            if chosen_key is None or key > chosen_key:
                chosen_key, chosen_i = key, i

        trial = include(selected, banned, value, cost, chosen_i)
        exclude_banned = banned | reverse[chosen_i]
        include_first = False
        if trial is not None:
            include_first = (
                trial[2] > value or
                (trial[2] == value and trial[3] < cost) or
                (trial[2] == value and trial[3] == cost and
                 id_tuple(trial[0]) < id_tuple(selected))
            )
        if include_first:
            search(*trial)
            search(selected, exclude_banned, value, cost)
        else:
            search(selected, exclude_banned, value, cost)
            if trial is not None:
                search(*trial)

    search(required_mask, initial_banned, initial_value, initial_cost)
    return {"selected": list(best_ids), "value": best_value, "cost": list(best_cost)}
