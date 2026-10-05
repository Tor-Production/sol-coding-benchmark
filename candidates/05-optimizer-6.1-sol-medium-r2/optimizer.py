def solve(projects, budget, required=()):
    """Find the exact optimum using propagated, bounded binary search.

    All bounds are integer/rational relaxations; no floating point decisions
    affect correctness. Inputs are copied into private arrays after validation.
    """
    def integer(x):
        return isinstance(x, int) and not isinstance(x, bool)

    if (not isinstance(budget, list) or not 1 <= len(budget) <= 3
            or any(not integer(x) or x < 0 for x in budget)):
        raise ValueError("invalid budget")
    if not isinstance(projects, list) or len(projects) > 32:
        raise ValueError("invalid projects")
    if not isinstance(required, (list, tuple)):
        raise ValueError("invalid required IDs")
    dimensions = len(budget)
    keys = {'id', 'value', 'cost', 'requires', 'excludes'}
    ids = set()
    for p in projects:
        if not isinstance(p, dict) or set(p) != keys:
            raise ValueError("invalid project keys")
        name = p['id']
        if not isinstance(name, str) or not name or name in ids:
            raise ValueError("invalid project ID")
        ids.add(name)
        if not integer(p['value']):
            raise ValueError("invalid value")
        if (not isinstance(p['cost'], list) or len(p['cost']) != dimensions
                or any(not integer(x) or x < 0 for x in p['cost'])):
            raise ValueError("invalid cost")
        for field in ('requires', 'excludes'):
            refs = p[field]
            if (not isinstance(refs, list)
                    or any(not isinstance(x, str) for x in refs)
                    or len(set(refs)) != len(refs)):
                raise ValueError("invalid references")
    for p in projects:
        for field in ('requires', 'excludes'):
            if any(x not in ids or x == p['id'] for x in p[field]):
                raise ValueError("unknown or self reference")
    if (any(not isinstance(x, str) or x not in ids for x in required)
            or len(set(required)) != len(required)):
        raise ValueError("invalid required IDs")

    ordered = sorted(projects, key=lambda p: p['id'])
    names = [p['id'] for p in ordered]
    n = len(names)
    index = {name: i for i, name in enumerate(names)}
    values = [p['value'] for p in ordered]
    costs = [tuple(p['cost']) for p in ordered]
    closures = [0] * n
    visiting = [0] * n

    def closure(i):
        if visiting[i] == 1:
            raise ValueError("dependency cycle")
        if visiting[i] == 2:
            return closures[i]
        visiting[i] = 1
        mask = 1 << i
        for name in ordered[i]['requires']:
            mask |= closure(index[name])
        visiting[i] = 2
        closures[i] = mask
        return mask

    for i in range(n):
        closure(i)
    dependents = [0] * n
    conflicts = [0] * n
    for i in range(n):
        for j in range(n):
            if closures[j] & (1 << i):
                dependents[i] |= 1 << j
        for name in ordered[i]['excludes']:
            j = index[name]
            conflicts[i] |= 1 << j
            conflicts[j] |= 1 << i

    def bits(mask):
        while mask:
            bit = mask & -mask
            yield bit.bit_length() - 1
            mask ^= bit

    def totals(mask):
        value = 0
        cost = [0] * dimensions
        for i in bits(mask):
            value += values[i]
            for d in range(dimensions):
                cost[d] += costs[i][d]
        return value, tuple(cost)

    # Selecting i also bans all projects depending on any of its conflicts.
    bans = [0] * n
    impossible = 0
    for i in range(n):
        excluded = 0
        for j in bits(closures[i]):
            excluded |= conflicts[j]
        for j in bits(excluded):
            bans[i] |= dependents[j]
        _, c = totals(closures[i])
        if excluded & closures[i] or any(c[d] > budget[d] for d in range(dimensions)):
            impossible |= dependents[i]
    selected = 0
    for name in required:
        selected |= closures[index[name]]
    forbidden = impossible
    for i in bits(selected):
        forbidden |= bans[i]
    value, cost = totals(selected)
    if selected & forbidden or any(cost[d] > budget[d] for d in range(dimensions)):
        return None
    available = ((1 << n) - 1) & ~(selected | forbidden)
    best_value, best_cost = value, cost

    def id_tuple(mask):
        return tuple(names[i] for i in bits(mask))

    best_ids = id_tuple(selected)

    # Knapsack relaxations for each resource, plus a normalized surrogate.
    # Sorting uses exact fractions, including arbitrarily large input integers.
    from fractions import Fraction
    weights = [tuple(int(d == j) for d in range(dimensions))
               for j in range(dimensions)]
    if dimensions > 1:
        product = 1
        for b in budget:
            product *= max(1, b)
        weights.append(tuple(product // max(1, b) for b in budget))
    resources = []
    for w in weights:
        resource = [sum(w[d] * c[d] for d in range(dimensions)) for c in costs]
        order = sorted((i for i in range(n) if values[i] > 0),
                       key=lambda i: (resource[i] != 0,
                                      Fraction(resource[i], values[i]), i))
        capacity = sum(w[d] * budget[d] for d in range(dimensions))
        resources.append((w, resource, order, capacity))

    def search(left, chosen, val, used):
        nonlocal best_value, best_cost, best_ids
        chosen_ids = id_tuple(chosen)
        if (val > best_value or (val == best_value
                and (used < best_cost or (used == best_cost and chosen_ids < best_ids)))):
            best_value, best_cost, best_ids = val, used, chosen_ids
        if not left:
            return

        # Remove choices whose still-unselected dependency bundle cannot fit.
        for i in list(bits(left)):
            if not left & (1 << i):
                continue
            bundle = closures[i] & ~chosen
            if bundle & ~left:
                left &= ~dependents[i]
                continue
            _, extra = totals(bundle)
            if any(used[d] + extra[d] > budget[d] for d in range(dimensions)):
                left &= ~dependents[i]
        if not left:
            return
        upper = val + sum(max(0, values[i]) for i in bits(left))
        lower_cost = list(used)
        needed = best_value - val
        for r, (w, resource, order, capacity) in enumerate(resources):
            remaining = capacity - sum(w[d] * used[d] for d in range(dimensions))
            gain = 0
            need = needed
            minimum = 0
            for i in order:
                if not left & (1 << i):
                    continue
                v, c = values[i], resource[i]
                if need > 0:
                    take = min(need, v)
                    # Ceiling of the fractional minimum cost, only at the
                    # final item; preceding items were taken in full.
                    minimum += (take * c + v - 1) // v
                    need -= take
                if c <= remaining:
                    remaining -= c
                    gain += v
                else:
                    gain += v * remaining // c
                    break
            upper = min(upper, val + gain)
            if upper < best_value:
                return
            if r < dimensions and needed > 0:
                lower_cost[r] += minimum
        # A disjoint clique cover supplies another valid value upper bound.
        uncovered = left
        clique_gain = 0
        while uncovered:
            first = (uncovered & -uncovered).bit_length() - 1
            clique = 1 << first
            candidates = uncovered & conflicts[first]
            while candidates:
                j = (candidates & -candidates).bit_length() - 1
                clique |= 1 << j
                candidates &= conflicts[j]
            clique_gain += max(0, max(values[i] for i in bits(clique)))
            uncovered &= ~clique
        upper = min(upper, val + clique_gain)
        if upper < best_value:
            return
        if upper == best_value:
            lower = tuple(lower_cost)
            if lower > best_cost:
                return
            if lower == best_cost:
                # Smallest ID tuple in a relaxation that needs enough positive
                # value and contains all already chosen IDs. Earlier optional
                # IDs are always included until the tuple can legally end.
                optimistic = chosen
                need = max(0, needed)
                last = chosen.bit_length() - 1
                for i in bits(left):
                    if i <= last or need > 0:
                        optimistic |= 1 << i
                        need -= max(0, values[i])
                if id_tuple(optimistic) >= best_ids:
                    return

        # Prefer valuable dependency bundles and highly constrained choices.
        def priority(i):
            bundle = closures[i] & left
            gain, extra = totals(bundle)
            load = sum(Fraction(extra[d], max(1, budget[d] - used[d]))
                       for d in range(dimensions))
            return (gain > 0, Fraction(gain, 1) / (1 + load),
                    (bans[i] & left).bit_count(), -i)

        i = max(bits(left), key=priority)
        bundle = closures[i] & left
        gain, extra = totals(bundle)
        new_cost = tuple(used[d] + extra[d] for d in range(dimensions))
        if not bans[i] & chosen and not bans[i] & bundle:
            search(left & ~(bundle | bans[i]), chosen | bundle, val + gain, new_cost)
        search(left & ~dependents[i], chosen, val, used)

    search(available, selected, value, cost)
    return {'selected': list(best_ids), 'value': best_value, 'cost': list(best_cost)}
