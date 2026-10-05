from fractions import Fraction


def solve(projects, budget, required=()):
    """Find the exact optimum using closure propagation and branch and bound."""
    def integer(x):
        return isinstance(x, int) and not isinstance(x, bool)

    def invalid():
        raise ValueError("Invalid portfolio input")

    if not isinstance(budget, list) or not 1 <= len(budget) <= 3:
        invalid()
    if any(not integer(x) or x < 0 for x in budget):
        invalid()
    if not isinstance(projects, list) or len(projects) > 32:
        invalid()
    if not isinstance(required, (list, tuple)):
        invalid()
    ids = set()
    for p in projects:
        if not isinstance(p, dict) or set(p) != {'id', 'value', 'cost', 'requires', 'excludes'}:
            invalid()
        name = p['id']
        if not isinstance(name, str) or not name or name in ids:
            invalid()
        ids.add(name)
        if not integer(p['value']):
            invalid()
        c = p['cost']
        if not isinstance(c, list) or len(c) != len(budget):
            invalid()
        if any(not integer(x) or x < 0 for x in c):
            invalid()
        for field in ('requires', 'excludes'):
            refs = p[field]
            if not isinstance(refs, list) or any(not isinstance(x, str) for x in refs):
                invalid()
            if len(set(refs)) != len(refs) or name in refs:
                invalid()
    for p in projects:
        if any(x not in ids for x in p['requires'] + p['excludes']):
            invalid()
    if any(not isinstance(x, str) or x not in ids for x in required):
        invalid()
    if len(set(required)) != len(required):
        invalid()

    ps = sorted(projects, key=lambda p: p['id'])
    names = [p['id'] for p in ps]
    index = {name: i for i, name in enumerate(names)}
    n, d = len(ps), len(budget)
    values = [p['value'] for p in ps]
    costs = [tuple(p['cost']) for p in ps]
    closures = [0] * n
    visiting = set()

    def closure(i):
        if i in visiting:
            invalid()
        if closures[i]:
            return closures[i]
        visiting.add(i)
        mask = 1 << i
        for name in ps[i]['requires']:
            mask |= closure(index[name])
        visiting.remove(i)
        closures[i] = mask
        return mask

    for i in range(n):
        closure(i)
    conflicts = [0] * n
    dependents = [0] * n
    for i, p in enumerate(ps):
        for name in p['excludes']:
            j = index[name]
            conflicts[i] |= 1 << j
            conflicts[j] |= 1 << i
        for j in range(n):
            if closures[i] >> j & 1:
                dependents[j] |= 1 << i

    def bits(mask):
        while mask:
            bit = mask & -mask
            yield bit.bit_length() - 1
            mask ^= bit

    def totals(mask):
        rows = list(bits(mask))
        return sum(values[i] for i in rows), tuple(sum(costs[i][k] for i in rows) for k in range(d))

    def forbidden(mask):
        out = 0
        for i in bits(mask):
            out |= conflicts[i]
        expanded = 0
        for i in bits(out):
            expanded |= dependents[i]
        return expanded

    selected = 0
    for name in required:
        selected |= closures[index[name]]
    value, cost = totals(selected)
    if forbidden(selected) & selected or any(cost[k] > budget[k] for k in range(d)):
        return None
    available = ((1 << n) - 1) & ~selected & ~forbidden(selected)
    for i in range(n):
        _, c = totals(closures[i])
        if forbidden(closures[i]) & closures[i] or any(c[k] > budget[k] for k in range(d)):
            available &= ~dependents[i]

    positive = [i for i in range(n) if values[i] > 0]
    orders = [sorted(positive, key=lambda i: (costs[i][k] != 0,
               Fraction(costs[i][k], values[i]), i)) for k in range(d)]
    best = (value, cost, tuple(names[i] for i in bits(selected)))

    def search(sel, avail, val, used):
        nonlocal best
        chosen = tuple(names[i] for i in bits(sel))
        if val > best[0] or (val == best[0] and (used, chosen) < (best[1], best[2])):
            best = val, used, chosen
        upper = val + sum(values[i] for i in positive if avail >> i & 1)
        lower_cost = []
        for k, order in enumerate(orders):
            room = budget[k] - used[k]
            bound = val
            need = max(0, best[0] - val)
            minimum = Fraction(used[k])
            for i in order:
                if not (avail >> i & 1):
                    continue
                v, c = values[i], costs[i][k]
                if need:
                    take = min(need, v)
                    minimum += Fraction(take * c, v)
                    need -= take
                if c <= room:
                    bound += v
                    room -= c
                else:
                    bound += (v * room) // c
                    room = 0
            upper = min(upper, bound)
            lower_cost.append((minimum.numerator + minimum.denominator - 1) // minimum.denominator)
        if upper < best[0]:
            return
        if upper == best[0]:
            low = tuple(lower_cost)
            if low > best[1]:
                return
            if low == best[1]:
                # Smallest tuple among all supersets of sel drawn from avail.
                last = sel.bit_length() - 1
                optimistic = tuple(names[i] for i in bits(sel | (avail & ((1 << (last + 1)) - 1))))
                if optimistic >= best[2]:
                    return
        if not avail:
            return
        # Branch on a valuable closure; both branches partition all completions.
        i = max(bits(avail), key=lambda j: (values[j], -j))
        added = closures[i] & ~sel
        if added & ~(avail | sel) == 0:
            gain, extra = totals(added)
            newcost = tuple(used[k] + extra[k] for k in range(d))
            banned = forbidden(added)
            if not banned & (sel | added) and all(newcost[k] <= budget[k] for k in range(d)):
                search(sel | added, avail & ~added & ~banned, val + gain, newcost)
        search(sel, avail & ~dependents[i], val, used)

    search(selected, available, value, cost)
    return {'selected': list(best[2]), 'value': best[0], 'cost': list(best[1])}
