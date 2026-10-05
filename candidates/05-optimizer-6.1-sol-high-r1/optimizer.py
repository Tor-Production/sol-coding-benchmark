"""Exact portfolio optimization using meet-in-the-middle and bitset indexes."""

from bisect import bisect_right
from functools import cmp_to_key, lru_cache


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _validate(projects, budget, required):
    if not isinstance(projects, list) or len(projects) > 32:
        raise ValueError("projects must be a list of at most 32 dictionaries")
    if (not isinstance(budget, list) or not 1 <= len(budget) <= 3
            or any(not _integer(x) or x < 0 for x in budget)):
        raise ValueError("invalid budget")
    if not isinstance(required, (list, tuple)):
        raise ValueError("required must be a list or tuple")
    ids = set()
    keys = {"id", "value", "cost", "requires", "excludes"}
    for project in projects:
        if not isinstance(project, dict) or set(project) != keys:
            raise ValueError("invalid project keys")
        name = project["id"]
        if not isinstance(name, str) or not name or name in ids:
            raise ValueError("invalid or duplicate project ID")
        ids.add(name)
        if not _integer(project["value"]):
            raise ValueError("invalid value")
        cost = project["cost"]
        if (not isinstance(cost, list) or len(cost) != len(budget)
                or any(not _integer(x) or x < 0 for x in cost)):
            raise ValueError("invalid cost")
    for project in projects:
        for field in ("requires", "excludes"):
            refs = project[field]
            if not isinstance(refs, list):
                raise ValueError("references must be lists")
            seen = set()
            for ref in refs:
                if (not isinstance(ref, str) or ref not in ids
                        or ref == project["id"] or ref in seen):
                    raise ValueError("invalid project reference")
                seen.add(ref)
    seen = set()
    for ref in required:
        if not isinstance(ref, str) or ref not in ids or ref in seen:
            raise ValueError("invalid required ID")
        seen.add(ref)

    ordered = sorted(projects, key=lambda p: p["id"])
    index = {p["id"]: i for i, p in enumerate(ordered)}
    dependencies = [0] * len(ordered)
    exclusions = [0] * len(ordered)
    state = [0] * len(ordered)

    def visit(i):
        if state[i] == 1:
            raise ValueError("cyclic dependencies")
        if state[i] == 2:
            return dependencies[i]
        state[i] = 1
        mask = 0
        for ref in ordered[i]["requires"]:
            j = index[ref]
            mask |= (1 << j) | visit(j)
        dependencies[i] = mask
        state[i] = 2
        return mask

    for i, project in enumerate(ordered):
        visit(i)
        for ref in project["excludes"]:
            j = index[ref]
            exclusions[i] |= 1 << j
            exclusions[j] |= 1 << i
    mandatory = 0
    for ref in required:
        i = index[ref]
        mandatory |= (1 << i) | dependencies[i]
    return ordered, dependencies, exclusions, mandatory


def _halves(projects, dependencies, exclusions, mandatory, budget, start, end):
    """Enumerate subsets closed and conflict-free within this half.

    Cross-half requirements and conflicts are retained as global masks.
    Nonnegative costs let us stop growing any over-budget subset.
    """
    size = end - start
    local = ((1 << size) - 1) << start
    needed = (mandatory & local) >> start
    records = []

    def walk(i, mask, value, cost, deps, bans):
        if i == size:
            if mask & needed == needed and (deps & local) >> start & ~mask == 0:
                records.append((mask, value, cost, deps & ~local, bans & ~local))
            return
        bit = 1 << i
        global_bit = bit << start
        # Once a lower-index dependency has been omitted, adding its dependent
        # cannot produce a closed subset.
        if not needed & bit and not deps & global_bit:
            walk(i + 1, mask, value, cost, deps, bans)
        p = projects[start + i]
        new_deps = deps | dependencies[start + i]
        new_bans = bans | exclusions[start + i]
        new_mask = mask | bit
        if (bans & global_bit or new_bans & (new_mask << start)
                or ((new_deps & local) >> start) & (bit - 1) & ~mask):
            return
        new_cost = tuple(a + b for a, b in zip(cost, p["cost"]))
        if any(a > b for a, b in zip(new_cost, budget)):
            return
        walk(i + 1, new_mask, value + p["value"], new_cost, new_deps, new_bans)

    walk(0, 0, 0, (0,) * len(budget), 0, 0)
    return records


def _cost_index(records, dimension, universe):
    """Index cost ranks by binary digit, using O(N log N) bits.

    A prefix of numeric ranks is a Boolean expression on those digits;
    Python's integer operations evaluate it for all records at once.
    """
    costs = sorted({r[2][dimension] for r in records})
    ranks = {cost: rank for rank, cost in enumerate(costs)}
    width = (len(costs) - 1).bit_length()
    planes = [bytearray((len(records) + 7) // 8) for _ in range(width)]
    for i, record in enumerate(records):
        rank = ranks[record[2][dimension]]
        while rank:
            bit = rank & -rank
            planes[bit.bit_length() - 1][i >> 3] |= 1 << (i & 7)
            rank ^= bit
    zeros = [universe ^ int.from_bytes(p, "little") for p in planes]

    @lru_cache(maxsize=128)
    def prefix(rank):
        if rank < 0:
            return 0
        if rank >= len(costs) - 1:
            return universe
        result = universe
        for bit, zero in enumerate(zeros):
            if rank & (1 << bit):
                result |= zero
            else:
                result &= zero
        return result

    def query(limit):
        return prefix(bisect_right(costs, limit) - 1)

    return query


def _value_bound(projects, weights):
    """An exact fractional-knapsack upper bound for a budget relaxation.

    Discarding dependencies, conflicts and negative values only enlarges the
    feasible region. Nonnegative weights combine budgets into one necessary
    constraint. Integer arithmetic avoids unsafe rounding of the bound.
    """
    free = 0
    items = []
    for p in projects:
        if p["value"] <= 0:
            continue
        cost = sum(a * b for a, b in zip(weights, p["cost"]))
        if cost == 0:
            free += p["value"]
        else:
            items.append((cost, p["value"]))

    def compare(a, b):
        cross = b[1] * a[0] - a[1] * b[0]
        return (cross > 0) - (cross < 0)

    items.sort(key=cmp_to_key(compare))
    costs, values = [0], [free]
    for cost, value in items:
        costs.append(costs[-1] + cost)
        values.append(values[-1] + value)

    def bound(capacity):
        i = bisect_right(costs, capacity) - 1
        result = values[i]
        if i < len(items):
            cost, value = items[i]
            result += (capacity - costs[i]) * value // cost
        return result

    return bound


def solve(projects, budget, required=()):
    """Return the globally best feasible portfolio, or None if infeasible."""
    ordered, deps, bans, mandatory = _validate(projects, budget, required)
    n = len(ordered)
    # Validation, including cycle detection, always precedes feasibility tests.
    mandatory_cost = tuple(sum(p["cost"][d] for i, p in enumerate(ordered)
                               if mandatory & (1 << i))
                           for d in range(len(budget)))
    if (any(a > b for a, b in zip(mandatory_cost, budget))
            or any(bans[i] & mandatory for i in range(n) if mandatory & (1 << i))):
        return None
    if not mandatory and all(p["value"] <= 0 for p in ordered):
        return {"selected": [], "value": 0, "cost": [0] * len(budget)}

    split = n // 2
    left = _halves(ordered, deps, bans, mandatory, budget, 0, split)
    right = _halves(ordered, deps, bans, mandatory, budget, split, n)
    if not left or not right:
        return None

    def selected_indices(mask):
        return tuple(i for i in range(n - split) if mask & (1 << i))

    # Better right records have higher bit positions. All left IDs precede
    # all right IDs, so right-ID ordering also orders unions with a fixed left.
    right.sort(key=lambda r: (-r[1], r[2], selected_indices(r[0])), reverse=True)
    universe = (1 << len(right)) - 1
    membership = [bytearray((len(right) + 7) // 8) for _ in range(n - split)]
    for rank, record in enumerate(right):
        mask = record[0]
        while mask:
            bit = mask & -mask
            membership[bit.bit_length() - 1][rank >> 3] |= 1 << (rank & 7)
            mask ^= bit
    membership = [int.from_bytes(p, "little") for p in membership]
    absent = [universe ^ p for p in membership]
    cost_queries = [_cost_index(right, d, universe) for d in range(len(budget))]

    # Omitting a left project forbids every right project depending on it.
    dependents = [0] * split
    for j in range(split, n):
        mask = deps[j] & ((1 << split) - 1)
        while mask:
            bit = mask & -mask
            dependents[bit.bit_length() - 1] |= 1 << (j - split)
            mask ^= bit

    best = None
    best_ids = None
    max_right_value = right[-1][1]
    # Cheap relaxations remove left records that cannot improve the incumbent.
    # Besides each individual budget, use a normalized sum of the budgets.
    dimensions = len(budget)
    weight_vectors = [tuple(int(i == d) for i in range(dimensions))
                      for d in range(dimensions)]
    if dimensions > 1:
        product = 1
        for limit in budget:
            product *= max(1, limit)
        weight_vectors.append(tuple(product // max(1, limit) for limit in budget))
    bounds = [(weights, _value_bound(ordered[split:], weights))
              for weights in weight_vectors]
    ranked_left = []
    for record in left:
        remaining = tuple(b - c for b, c in zip(budget, record[2]))
        upper = min([max_right_value] +
                    [bound(sum(a * b for a, b in zip(weights, remaining)))
                     for weights, bound in bounds])
        ranked_left.append((record[1] + upper, record))
    ranked_left.sort(key=lambda entry: entry[0], reverse=True)
    all_left = (1 << split) - 1
    for upper, (lmask, lvalue, lcost, need, ban) in ranked_left:
        if best is not None and upper < best[0]:
            break
        forbidden = ban >> split
        missing = all_left ^ lmask
        while missing:
            bit = missing & -missing
            forbidden |= dependents[bit.bit_length() - 1]
            missing ^= bit
        need >>= split
        if need & forbidden:
            continue
        candidates = universe
        mask = need | forbidden
        while mask and candidates:
            bit = mask & -mask
            i = bit.bit_length() - 1
            candidates &= membership[i] if need & bit else absent[i]
            mask ^= bit
        for d, query in enumerate(cost_queries):
            if not candidates:
                break
            candidates &= query(budget[d] - lcost[d])
        if not candidates:
            continue
        rmask, rvalue, rcost, _, _ = right[candidates.bit_length() - 1]
        value = lvalue + rvalue
        cost = tuple(a + b for a, b in zip(lcost, rcost))
        if best is not None and (value < best[0] or value == best[0] and cost > best[1]):
            continue
        mask = lmask | (rmask << split)
        ids = tuple(p["id"] for i, p in enumerate(ordered) if mask & (1 << i))
        if (best is None or value > best[0] or cost < best[1]
                or cost == best[1] and ids < best_ids):
            best = (value, cost)
            best_ids = ids
    if best is None:
        return None
    return {"selected": list(best_ids), "value": best[0], "cost": list(best[1])}
