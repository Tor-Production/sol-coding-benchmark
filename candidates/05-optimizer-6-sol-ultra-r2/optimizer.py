"""Exact optimization of small portfolios with budgets and logical constraints."""

from bisect import bisect_left, bisect_right
from functools import cmp_to_key, lru_cache
from itertools import combinations
from math import lcm


def _bits(mask):
    while mask:
        bit = mask & -mask
        yield bit.bit_length() - 1
        mask ^= bit


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _validate(projects, budget, required):
    if not isinstance(projects, list) or len(projects) > 32:
        raise ValueError("projects must be a list of at most 32 dictionaries")
    if (not isinstance(budget, list) or not 1 <= len(budget) <= 3 or
            any(not _integer(x) or x < 0 for x in budget)):
        raise ValueError("invalid budget")
    if not isinstance(required, (list, tuple)):
        raise ValueError("required must be a list or tuple")

    fields = {"id", "value", "cost", "requires", "excludes"}
    seen = set()
    for project in projects:
        if not isinstance(project, dict) or set(project) != fields:
            raise ValueError("invalid project dictionary")
        name = project["id"]
        if not isinstance(name, str) or not name or name in seen:
            raise ValueError("invalid or duplicate project ID")
        seen.add(name)
        if not _integer(project["value"]):
            raise ValueError("project value must be an integer")
        cost = project["cost"]
        if (not isinstance(cost, list) or len(cost) != len(budget) or
                any(not _integer(x) or x < 0 for x in cost)):
            raise ValueError("invalid project cost")
        for field in ("requires", "excludes"):
            refs = project[field]
            if (not isinstance(refs, list) or
                    any(not isinstance(x, str) or not x or x == name
                        for x in refs) or len(set(refs)) != len(refs)):
                raise ValueError("invalid project references")

    names = sorted(seen)
    index = {name: i for i, name in enumerate(names)}
    by_name = {project["id"]: project for project in projects}
    values = [by_name[name]["value"] for name in names]
    costs = [tuple(by_name[name]["cost"]) for name in names]
    needs = []
    conflicts = [0] * len(names)
    for i, name in enumerate(names):
        project = by_name[name]
        need = 0
        for ref in project["requires"]:
            if ref not in index:
                raise ValueError("unknown required project")
            need |= 1 << index[ref]
        needs.append(need)
        for ref in project["excludes"]:
            if ref not in index:
                raise ValueError("unknown excluded project")
            j = index[ref]
            conflicts[i] |= 1 << j
            conflicts[j] |= 1 << i

    required_mask = 0
    for name in required:
        if not isinstance(name, str) or name not in index:
            raise ValueError("invalid required ID")
        bit = 1 << index[name]
        if required_mask & bit:
            raise ValueError("duplicate required ID")
        required_mask |= bit

    state = [0] * len(names)
    closure = [0] * len(names)

    def visit(i):
        if state[i] == 1:
            raise ValueError("dependency cycle")
        if state[i] == 2:
            return closure[i]
        state[i] = 1
        mask = 1 << i
        for j in _bits(needs[i]):
            mask |= visit(j)
        closure[i] = mask
        state[i] = 2
        return mask

    for i in range(len(names)):
        visit(i)
    return names, values, costs, needs, conflicts, closure, tuple(budget), required_mask


def _meet_in_middle(names, values, costs, budget, required_mask,
                    needs, conflicts, closure, core_mask):
    """Meet in the middle when all logical constraints fit in one half."""
    n, dims = len(names), len(budget)
    zeros = (0,) * dims
    base_mask = 0
    for i in _bits(required_mask):
        base_mask |= closure[i]
    if any(conflicts[i] & base_mask for i in _bits(base_mask)):
        return None
    base_cost = tuple(sum(costs[i][d] for i in _bits(base_mask))
                      for d in range(dims))
    if any(base_cost[d] > budget[d] for d in range(dims)):
        return None
    base_value = sum(values[i] for i in _bits(base_mask))
    remaining = tuple(budget[d] - base_cost[d] for d in range(dims))
    optional_zeros = []
    core_items = []
    items = []
    for i in range(n):
        bit = 1 << i
        if base_mask & bit:
            continue
        if core_mask & bit:
            core_items.append(i)
            continue
        if values[i] < 0 or (values[i] == 0 and any(costs[i])):
            continue
        if values[i] == 0:
            optional_zeros.append(i)
        elif not any(costs[i]):
            base_mask |= bit
            base_value += values[i]
        elif all(costs[i][d] <= remaining[d] for d in range(dims)):
            items.append(i)

    def states(part):
        # The best state at each exact cost dominates all other states there.
        result = {zeros: (0, 0, 0)}  # value, selected mask, lexicographic bits
        for i in part:
            item_cost = costs[i]
            bit = 1 << i
            lex_bit = 1 << (n - 1 - i)
            for cost, (value, mask, lex) in list(result.items()):
                new_cost = tuple(cost[d] + item_cost[d] for d in range(dims))
                if any(new_cost[d] > remaining[d] for d in range(dims)):
                    continue
                new_value, new_lex = value + values[i], lex | lex_bit
                old = result.get(new_cost)
                if old is None or (new_value, new_lex) > (old[0], old[2]):
                    result[new_cost] = (new_value, mask | bit, new_lex)
        return [(cost, *record) for cost, record in result.items()]

    total = len(core_items) + len(items)
    left_size = max(len(core_items), (total + 1) // 2)
    left_items = core_items + items[:left_size - len(core_items)]
    right_items = items[left_size - len(core_items):]
    if core_items:
        # Keep all feasible left portfolios: zero or negative core values can
        # make equal-cost states incomparable under the final ID tie break.
        left = [(zeros, 0, 0, 0)]
        for i in left_items:
            bit = 1 << i
            lex_bit = 1 << (n - 1 - i)
            additions = []
            for cost, value, mask, lex in left:
                new_cost = tuple(cost[d] + costs[i][d] for d in range(dims))
                if all(new_cost[d] <= remaining[d] for d in range(dims)):
                    additions.append((new_cost, value + values[i],
                                      mask | bit, lex | lex_bit))
            left.extend(additions)
        feasible = []
        for state in left:
            mask = base_mask | state[2]
            if all(not needs[i] & ~mask and not conflicts[i] & mask
                   for i in _bits(mask & core_mask)):
                feasible.append(state)
        left = feasible
    else:
        left = states(left_items)
    right = states(right_items)
    return _join_halves(names, budget, base_mask, base_value, base_cost,
                        optional_zeros, left, right, bool(core_items))


def _join_halves(names, budget, base_mask, base_value, base_cost,
                 optional_zeros, left, right, check_actual_lex):
    """Find the best budget-compatible pair of enumerated half portfolios."""
    n, dims = len(names), len(budget)
    zeros = (0,) * dims
    remaining = tuple(budget[d] - base_cost[d] for d in range(dims))
    right.sort(key=lambda state: state[0][0])
    ranks = [(state[1], *(-c for c in state[0]), state[3]) for state in right]
    queries = sorted(left, key=lambda state: remaining[0] - state[0][0])

    if dims >= 2:
        xcoords = sorted({state[0][1] for state in right})
        size = len(xcoords)
        if dims == 2:
            tree = [-1] * (size + 1)
        else:
            # An offline Fenwick tree of Fenwick trees for two prefix limits.
            ycoords = [[] for _ in range(size + 1)]
            for state in right:
                x = bisect_left(xcoords, state[0][1]) + 1
                while x <= size:
                    ycoords[x].append(state[0][2])
                    x += x & -x
            ycoords = [sorted(set(ys)) for ys in ycoords]
            tree = [[-1] * (len(ys) + 1) for ys in ycoords]

    def better(a, b):
        return b < 0 or ranks[a] > ranks[b]

    def insert(k):
        if dims == 1:
            return
        x = bisect_left(xcoords, right[k][0][1]) + 1
        if dims == 2:
            while x <= size:
                if better(k, tree[x]):
                    tree[x] = k
                x += x & -x
        else:
            yvalue = right[k][0][2]
            while x <= size:
                ys, row = ycoords[x], tree[x]
                y = bisect_left(ys, yvalue) + 1
                while y < len(row):
                    if better(k, row[y]):
                        row[y] = k
                    y += y & -y
                x += x & -x

    def query(cap):
        if dims == 1:
            return current_best
        x = bisect_right(xcoords, cap[1])
        answer = -1
        if dims == 2:
            while x:
                k = tree[x]
                if k >= 0 and better(k, answer):
                    answer = k
                x -= x & -x
        else:
            while x:
                ys, row = ycoords[x], tree[x]
                y = bisect_right(ys, cap[2])
                while y:
                    k = row[y]
                    if k >= 0 and better(k, answer):
                        answer = k
                    y -= y & -y
                x -= x & -x
        return answer

    pointer = 0
    current_best = -1
    best_rank = None
    best_mask = 0
    best_cost = zeros
    best_ids = None
    base_lex = sum(1 << (n - 1 - i) for i in _bits(base_mask))
    for a in queries:
        cap = tuple(remaining[d] - a[0][d] for d in range(dims))
        while pointer < len(right) and right[pointer][0][0] <= cap[0]:
            if dims == 1 and better(pointer, current_best):
                current_best = pointer
            else:
                insert(pointer)
            pointer += 1
        k = query(cap)
        if k < 0:
            continue
        b = right[k]
        total_cost = tuple(base_cost[d] + a[0][d] + b[0][d]
                           for d in range(dims))
        rank = (base_value + a[1] + b[1],
                *(-c for c in total_cost), base_lex | a[3] | b[3])
        candidate_mask = base_mask | a[2] | b[2]
        if check_actual_lex:
            if candidate_mask:
                last = candidate_mask.bit_length() - 1
                for i in optional_zeros:
                    if i < last:
                        candidate_mask |= 1 << i
            ids = tuple(names[i] for i in _bits(candidate_mask))
            is_better = (best_rank is None or rank[:-1] > best_rank[:-1] or
                         (rank[:-1] == best_rank[:-1] and ids < best_ids))
        else:
            is_better = best_rank is None or rank > best_rank
        if is_better:
            best_rank = rank
            best_mask = candidate_mask
            best_cost = total_cost
            if check_actual_lex:
                best_ids = ids

    if best_rank is None:
        return None
    if best_mask and not check_actual_lex:
        last = best_mask.bit_length() - 1
        for i in optional_zeros:
            if i < last:
                best_mask |= 1 << i
    return {"selected": [names[i] for i in _bits(best_mask)],
            "value": best_rank[0], "cost": list(best_cost)}


def _component_mitm(names, values, costs, budget, required_mask, needs,
                    conflicts, closure, core_mask):
    """Use two independent constraint components groups when they fit."""
    n, dims = len(names), len(budget)
    zeros = (0,) * dims
    base_mask = 0
    for i in _bits(required_mask):
        base_mask |= closure[i]
    if any(conflicts[i] & base_mask for i in _bits(base_mask)):
        return None
    base_cost = tuple(sum(costs[i][d] for i in _bits(base_mask))
                      for d in range(dims))
    if any(base_cost[d] > budget[d] for d in range(dims)):
        return None
    base_value = sum(values[i] for i in _bits(base_mask))
    remaining = tuple(budget[d] - base_cost[d] for d in range(dims))
    optional_zeros = []
    active = 0
    for i in range(n):
        bit = 1 << i
        if base_mask & bit:
            continue
        if not core_mask & bit:
            if values[i] < 0 or (values[i] == 0 and any(costs[i])):
                continue
            if values[i] == 0:
                optional_zeros.append(i)
                continue
            if not any(costs[i]):
                base_mask |= bit
                base_value += values[i]
                continue
            if any(costs[i][d] > remaining[d] for d in range(dims)):
                continue
        active |= bit

    descendants = [0] * n
    for j in _bits(active):
        for i in _bits(closure[j]):
            descendants[i] |= 1 << j
    impossible = 0
    for i in _bits(active):
        added = closure[i] & ~base_mask
        if any(sum(costs[j][d] for j in _bits(added)) > remaining[d]
               for d in range(dims)):
            impossible |= descendants[i]
    active &= ~impossible
    while True:
        useless = 0
        for i in _bits(active):
            if ((values[i] < 0 or (values[i] == 0 and any(costs[i])))
                    and descendants[i] & active == (1 << i)):
                useless |= 1 << i
        if not useless:
            break
        active &= ~useless
    if any(values[i] <= 0 for i in _bits(active)):
        return False

    reverse_needs = [0] * n
    for i in range(n):
        for j in _bits(needs[i]):
            reverse_needs[j] |= 1 << i
    unseen = active
    components = []
    while unseen:
        component = unseen & -unseen
        frontier = component
        while frontier:
            neighbors = 0
            for i in _bits(frontier):
                neighbors |= needs[i] | reverse_needs[i] | conflicts[i]
            frontier = neighbors & unseen & ~component
            component |= frontier
        unseen &= ~component
        components.append(component)

    total = active.bit_count()
    ways = {0: 0}
    for j, component in enumerate(components):
        size = component.bit_count()
        for amount, component_bits in list(ways.items()):
            if amount + size <= 16 and amount + size not in ways:
                ways[amount + size] = component_bits | (1 << j)
    choices = [amount for amount in ways if total - 16 <= amount <= 16]
    if choices:
        amount = min(choices, key=lambda x: abs(total - 2 * x))
        left_group = 0
        for j in _bits(ways[amount]):
            left_group |= components[j]
    else:
        # A small cut is enough: enumerate each side and condition the range
        # queries on its few cross-boundary project decisions.
        neighbors = [(needs[i] | reverse_needs[i] | conflicts[i]) & active
                     for i in range(n)]
        vertices = list(_bits(active))
        target = (total + 1) // 2

        def cut_score(group):
            other = active & ~group
            cross = sum((neighbors[i] & other).bit_count()
                        for i in _bits(group))
            right_edge = sum(1 for i in _bits(other) if neighbors[i] & group)
            return right_edge, cross

        left_group = sum(1 << i for i in vertices[:target])
        best_cut = cut_score(left_group)
        for seed in vertices:
            group = 1 << seed
            while group.bit_count() < target:
                candidate = max((i for i in vertices if not group & (1 << i)),
                                key=lambda i: ((neighbors[i] & group).bit_count(),
                                               -(neighbors[i] & ~group).bit_count(),
                                               -i))
                group |= 1 << candidate
            score = cut_score(group)
            if score < best_cut:
                left_group, best_cut = group, score
        if best_cut[0] > 4 or best_cut[1] > 8:
            return False
    right_group = active & ~left_group
    left_boundary = sum(1 << i for i in _bits(left_group)
                        if (needs[i] | reverse_needs[i] | conflicts[i]) & right_group)
    right_boundary = sum(1 << i for i in _bits(right_group)
                         if (needs[i] | reverse_needs[i] | conflicts[i]) & left_group)

    def enumerate_group(group, boundary):
        states = [(zeros, 0, 0, 0)]
        for i in _bits(group):
            bit = 1 << i
            lex_bit = 1 << (n - 1 - i)
            additions = []
            for cost, value, mask, lex in states:
                new_cost = tuple(cost[d] + costs[i][d] for d in range(dims))
                if all(new_cost[d] <= remaining[d] for d in range(dims)):
                    additions.append((new_cost, value + values[i],
                                      mask | bit, lex | lex_bit))
            states.extend(additions)
        best_at_cost = {}
        for state in states:
            mask = base_mask | state[2]
            if any((needs[i] & group & ~mask) or conflicts[i] & mask
                   for i in _bits(state[2])):
                continue
            key = (state[0], state[2] & boundary)
            old = best_at_cost.get(key)
            if old is None or (state[1], state[3]) > (old[1], old[3]):
                best_at_cost[key] = state
        return list(best_at_cost.values())

    left = enumerate_group(left_group, left_boundary)
    right = enumerate_group(right_group, right_boundary)
    if not right_boundary:
        return _join_halves(names, budget, base_mask, base_value, base_cost,
                            optional_zeros, left, right, False)

    def compatible(left_bits, right_bits):
        for i in _bits(left_bits):
            if (needs[i] & right_group & ~(base_mask | right_bits) or
                    conflicts[i] & right_bits):
                return False
        for i in _bits(right_bits):
            if needs[i] & left_group & ~(base_mask | left_bits):
                return False
        return True

    right_categories = {}
    for state in right:
        right_categories.setdefault(state[2] & right_boundary, []).append(state)
    left_patterns = {state[2] & left_boundary for state in left}
    best_answer = None
    for right_bits, right_states in right_categories.items():
        allowed = {bits for bits in left_patterns if compatible(bits, right_bits)}
        left_states = [state for state in left
                       if state[2] & left_boundary in allowed]
        if not left_states:
            continue
        answer = _join_halves(names, budget, base_mask, base_value, base_cost,
                              optional_zeros, left_states, right_states, False)
        if answer is None:
            continue
        key = (-answer["value"], tuple(answer["cost"]),
               tuple(answer["selected"]))
        if best_answer is None or key < best_answer[0]:
            best_answer = key, answer
    return best_answer[1]


def _constrained(names, values, costs, needs, conflicts, closure, budget,
                 required_mask):
    n, dims = len(names), len(budget)
    all_mask = (1 << n) - 1
    zeros = (0,) * dims
    descendants = [0] * n
    for j in range(n):
        for i in _bits(closure[j]):
            descendants[i] |= 1 << j

    def spread(mask):
        result = 0
        for i in _bits(mask):
            result |= descendants[i]
        return result

    def has_conflict(mask):
        return any(conflicts[i] & mask for i in _bits(mask))

    @lru_cache(maxsize=200000)
    def aggregate(mask):
        if not mask:
            return 0, zeros, 0
        bit = mask & -mask
        i = bit.bit_length() - 1
        value, cost, blocked = aggregate(mask ^ bit)
        return (value + values[i],
                tuple(cost[d] + costs[i][d] for d in range(dims)),
                blocked | conflicts[i])

    invalid = 0
    for i in range(n):
        mask = closure[i]
        if has_conflict(mask) or any(
                aggregate(mask)[1][d] > budget[d] for d in range(dims)):
            invalid |= 1 << i
    invalid = spread(invalid)
    selected = 0
    for i in _bits(required_mask):
        selected |= closure[i]
    if selected & invalid or has_conflict(selected):
        return None
    score, spent, blocked = aggregate(selected)
    if any(spent[d] > budget[d] for d in range(dims)):
        return None
    removed = invalid | blocked
    available = all_mask & ~selected & ~spread(removed)

    best_value, best_cost, best_mask = score, spent, selected
    best_ids = tuple(names[i] for i in _bits(selected))

    def consider(mask, value, cost):
        nonlocal best_value, best_cost, best_mask, best_ids
        if value > best_value or (value == best_value and cost < best_cost):
            best_value, best_cost, best_mask = value, cost, mask
            best_ids = tuple(names[i] for i in _bits(mask))
        elif value == best_value and cost == best_cost:
            ids = tuple(names[i] for i in _bits(mask))
            if ids < best_ids:
                best_mask, best_ids = mask, ids

    # Weighted costs combine normalized budget dimensions for ordering/bounds.
    positive_dims = [d for d in range(dims) if budget[d] > 0]
    scale = lcm(*(budget[d] for d in positive_dims)) if positive_dims else 1
    normalized = [scale // budget[d] if budget[d] else 0
                  for d in range(dims)]
    priority = []
    for i in range(n):
        mask = closure[i]
        value, cost, blocked = aggregate(mask)
        degree = (blocked & available).bit_count()
        benefit = max(0, value) + max(0, values[i])
        weight = sum(cost[d] * normalized[d] for d in range(dims))
        priority.append((benefit * (n + 2 * degree), scale + weight))

    def compare_priority(i, j):
        left = priority[i][0] * priority[j][1]
        right = priority[j][0] * priority[i][1]
        if left != right:
            return -1 if left > right else 1
        return i - j

    order = sorted(range(n), key=cmp_to_key(compare_priority))
    for trial in (order, sorted(range(n), key=lambda i: -values[i]),
                  list(reversed(order))):
        mask, possible, value, used = selected, available, score, spent
        for i in trial:
            bit = 1 << i
            if not possible & bit:
                continue
            added = closure[i] & ~mask
            if added & ~possible:
                continue
            delta, extra, forbidden = aggregate(added)
            if delta <= 0 or any(used[d] + extra[d] > budget[d]
                                 for d in range(dims)):
                continue
            mask |= added
            value += delta
            used = tuple(used[d] + extra[d] for d in range(dims))
            possible &= ~spread(forbidden & possible)
            possible &= ~added
            consider(mask, value, used)

    positive_order = sorted((i for i in range(n) if values[i] > 0),
                            key=lambda i: -values[i])
    positive_mask = sum(1 << i for i in positive_order)
    relaxations = []
    for count in range(1, len(positive_dims) + 1):
        for group in combinations(positive_dims, count):
            common = lcm(*(budget[d] for d in group))
            multipliers = tuple(common // budget[d] if d in group else 0
                                for d in range(dims))
            weights = [sum(costs[i][d] * multipliers[d]
                           for d in range(dims)) for i in range(n)]

            def compare_ratio(i, j):
                wi, wj = weights[i], weights[j]
                if wi == 0 or wj == 0:
                    if wi == wj:
                        return i - j
                    return -1 if wi == 0 else 1
                cross_i, cross_j = values[i] * wj, values[j] * wi
                if cross_i != cross_j:
                    return -1 if cross_i > cross_j else 1
                return i - j

            ratio_order = sorted(positive_order, key=cmp_to_key(compare_ratio))
            relaxations.append((multipliers, weights, ratio_order))

    def simplify(mask, possible, value, used):
        while True:
            gone = 0
            for i in _bits(possible):
                added = closure[i] & ~mask
                if added & ~possible:
                    gone |= 1 << i
                    continue
                extra = aggregate(added)[1]
                if any(used[d] + extra[d] > budget[d] for d in range(dims)):
                    gone |= 1 << i
                elif (descendants[i] & possible) == (1 << i) and (
                        values[i] < 0 or
                        (values[i] == 0 and any(costs[i]))):
                    gone |= 1 << i
            if gone:
                possible &= ~spread(gone)
                continue
            forced = 0
            for i in _bits(possible):
                bit = 1 << i
                if (values[i] > 0 and not any(costs[i]) and
                        closure[i] & ~mask == bit and
                        not conflicts[i] & possible):
                    forced |= bit
            if forced:
                mask |= forced
                possible &= ~forced
                value += aggregate(forced)[0]
                continue
            return mask, possible, value, used

    def tie_can_improve(mask, possible, used):
        if used < best_cost:
            return True
        if used > best_cost:
            return False
        if not mask:
            return False  # The empty tuple is lexicographically first.
        last = mask.bit_length() - 1
        optimistic = mask
        for i in _bits(possible):
            if i < last and not any(costs[i]):
                optimistic |= 1 << i
        return tuple(names[i] for i in _bits(optimistic)) < best_ids

    def zero_finish(mask, possible, value, used):
        # With no positive value left, the current value and cost are optimal.
        # Greedily take each feasible free ID before the last chosen ID.
        if not mask:
            return
        for i in range(n):
            bit = 1 << i
            if i >= mask.bit_length() - 1 or not possible & bit:
                continue
            added = closure[i] & ~mask
            if added & ~possible:
                continue
            delta, extra, blocked = aggregate(added)
            if delta != 0 or any(extra) or blocked & (mask | added):
                continue
            mask |= added
            possible &= ~added
            possible &= ~spread(blocked & possible)
        consider(mask, value, used)

    def search(mask, possible, value, used):
        mask, possible, value, used = simplify(mask, possible, value, used)
        consider(mask, value, used)
        if not possible:
            return
        positives = possible & positive_mask
        if not positives:
            zero_finish(mask, possible, value, used)
            return

        upper = value + sum(values[i] for i in _bits(positives))
        if upper < best_value or (upper == best_value and
                                  not tie_can_improve(mask, possible, used)):
            return

        if any(conflicts[i] & positives for i in _bits(positives)):
            groups = []
            upper_conflict = value
            for i in positive_order:
                bit = 1 << i
                if not positives & bit:
                    continue
                for j, group in enumerate(groups):
                    if not group & ~conflicts[i]:
                        groups[j] |= bit
                        break
                else:
                    groups.append(bit)
                    upper_conflict += values[i]
            if upper_conflict < best_value or (
                    upper_conflict == best_value and
                    not tie_can_improve(mask, possible, used)):
                return

        for multipliers, weights, ratio_order in relaxations:
            capacity = sum((budget[d] - used[d]) * multipliers[d]
                           for d in range(dims))
            bound = value
            for i in ratio_order:
                if not positives & (1 << i):
                    continue
                weight = weights[i]
                if weight <= capacity:
                    capacity -= weight
                    bound += values[i]
                else:
                    bound += values[i] * capacity // weight
                    break
            if bound < best_value or (bound == best_value and
                                      not tie_can_improve(mask, possible, used)):
                return

        i = next(i for i in order if possible & (1 << i))
        added = closure[i] & ~mask
        delta, extra, forbidden = aggregate(added)
        if not added & ~possible and all(
                used[d] + extra[d] <= budget[d] for d in range(dims)):
            next_possible = possible & ~added
            next_possible &= ~spread(forbidden & next_possible)
            search(mask | added, next_possible, value + delta,
                   tuple(used[d] + extra[d] for d in range(dims)))
        search(mask, possible & ~descendants[i], value, used)

    search(selected, available, score, spent)
    return {"selected": list(best_ids), "value": best_value,
            "cost": list(best_cost)}


def solve(projects, budget, required=()):
    """Return the globally best feasible portfolio, or None."""
    data = _validate(projects, budget, required)
    names, values, costs, needs, conflicts, closure, limits, forced = data
    core_mask = 0
    for i in range(len(names)):
        if needs[i] or conflicts[i]:
            core_mask |= (1 << i) | needs[i] | conflicts[i]
    if core_mask.bit_count() <= 16:
        return _meet_in_middle(names, values, costs, limits, forced,
                               needs, conflicts, closure, core_mask)
    component_answer = _component_mitm(names, values, costs, limits, forced,
                                       needs, conflicts, closure, core_mask)
    if component_answer is not False:
        return component_answer
    return _constrained(names, values, costs, needs, conflicts, closure,
                        limits, forced)
