"""Exact portfolio optimization, using bit masks and admissible bounds."""
from bisect import bisect_left, bisect_right
from fractions import Fraction
from functools import lru_cache


def solve(projects, budget, required=()):
    integer = lambda x: isinstance(x, int) and not isinstance(x, bool)
    if not isinstance(budget, list) or not 1 <= len(budget) <= 3 or any(not integer(x) or x < 0 for x in budget):
        raise ValueError('invalid budget')
    if not isinstance(projects, list) or len(projects) > 32:
        raise ValueError('invalid projects')
    ids = []
    for p in projects:
        if not isinstance(p, dict) or set(p) != {'id', 'value', 'cost', 'requires', 'excludes'}:
            raise ValueError('invalid project keys')
        if not isinstance(p['id'], str) or not p['id'] or p['id'] in ids:
            raise ValueError('invalid id')
        ids.append(p['id'])
        if not integer(p['value']) or not isinstance(p['cost'], list) or len(p['cost']) != len(budget) or any(not integer(x) or x < 0 for x in p['cost']):
            raise ValueError('invalid value or cost')
    index = {s: i for i, s in enumerate(ids)}
    def refs(xs, own=None):
        if not isinstance(xs, (list, tuple) if own is None else list):
            raise ValueError('invalid references')
        seen = set()
        for s in xs:
            if not isinstance(s, str) or s not in index or s == own or s in seen:
                raise ValueError('invalid reference')
            seen.add(s)
        return sum(1 << index[s] for s in xs)
    req = refs(required)
    n = len(ids)
    deps = [refs(p['requires'], p['id']) for p in projects]
    exc = [refs(p['excludes'], p['id']) for p in projects]
    def bits(mask):
        while mask:
            b = mask & -mask
            yield b.bit_length() - 1
            mask -= b
    closure = [0] * n
    visiting = set()
    def visit(i):
        if i in visiting:
            raise ValueError('dependency cycle')
        if closure[i]:
            return closure[i]
        visiting.add(i)
        m = 1 << i
        for j in bits(deps[i]):
            m |= visit(j)
        visiting.remove(i)
        closure[i] = m
        return m
    for i in range(n):
        visit(i)
        for j in bits(exc[i]):
            exc[j] |= 1 << i
    values = [p['value'] for p in projects]
    costs = [tuple(p['cost']) for p in projects]
    d = len(budget)
    def totals(mask):
        members = list(bits(mask))
        return sum(values[i] for i in members), tuple(sum(costs[i][k] for i in members) for k in range(d))
    @lru_cache(maxsize=None)
    def names(mask):
        return tuple(sorted(ids[i] for i in bits(mask)))
    def rank(item):
        v, c, m = item
        return (-v, c, names(m))
    selected = 0
    for i in bits(req):
        selected |= closure[i]
    v0, c0 = totals(selected)
    if any(c0[k] > budget[k] for k in range(d)) or any(exc[i] & selected for i in bits(selected)):
        return None
    best = (v0, c0, selected)

    # Independent projects: offline orthogonal range maxima on the two halves.
    if not any(deps) and not any(exc):
        free = [i for i in range(n) if not selected >> i & 1 and values[i] == 0 and not any(costs[i])]
        active = [i for i in range(n) if not selected >> i & 1 and values[i] > 0]
        remaining = tuple(budget[k] - c0[k] for k in range(d))
        def enumerate_half(indices):
            out = [(0, (0,) * d, 0)]
            for i in indices:
                additions = []
                for v, c, m in out:
                    nc = tuple(c[k] + costs[i][k] for k in range(d))
                    if all(nc[k] <= remaining[k] for k in range(d)):
                        additions.append((v + values[i], nc, m | 1 << i))
                out.extend(additions)
            return out
        mid = len(active) // 2
        left = enumerate_half(active[:mid])
        right = sorted(enumerate_half(active[mid:]), key=lambda x: x[1][0])
        queries = sorted(left, key=lambda x: remaining[0] - x[1][0])
        def better(a, b):
            return b if a is None or (b is not None and rank(b) < rank(a)) else a
        ys = sorted({x[1][1] for x in right}) if d > 1 else [0]
        tree = [None] * (len(ys) + 1)
        zs = [[] for _ in tree]
        if d == 3:
            for item in right:
                j = bisect_left(ys, item[1][1]) + 1
                while j < len(tree):
                    zs[j].append(item[1][2]); j += j & -j
            zs = [sorted(set(z)) for z in zs]
            tree = [[None] * (len(z) + 1) for z in zs]
        pos = 0
        for lv, lc, lm in queries:
            limit = tuple(remaining[k] - lc[k] for k in range(d))
            while pos < len(right) and right[pos][1][0] <= limit[0]:
                item = right[pos]; pos += 1
                j = bisect_left(ys, item[1][1]) + 1 if d > 1 else 1
                while j < len(tree):
                    if d == 3:
                        z = bisect_left(zs[j], item[1][2]) + 1
                        while z < len(tree[j]):
                            tree[j][z] = better(tree[j][z], item); z += z & -z
                    else:
                        tree[j] = better(tree[j], item)
                    j += j & -j
            candidate = None
            j = bisect_right(ys, limit[1]) if d > 1 else 1
            while j:
                if d == 3:
                    z = bisect_right(zs[j], limit[2])
                    while z:
                        candidate = better(candidate, tree[j][z]); z -= z & -z
                else:
                    candidate = better(candidate, tree[j])
                j -= j & -j
            if candidate is not None:
                rv, rc, rm = candidate
                item = (v0 + lv + rv, tuple(c0[k] + lc[k] + rc[k] for k in range(d)), selected | lm | rm)
                if rank(item) < rank(best):
                    best = item
        # Cost-free zero values are included exactly when they improve the ID tuple.
        v, c, m = best
        if m:
            last = max(names(m))
            for i in free:
                if ids[i] < last:
                    m |= 1 << i
        best = v, c, m
    else:
        reverse = [sum(1 << j for j in range(n) if closure[j] >> i & 1) for i in range(n)]
        blocked = 0
        for i in bits(selected):
            for j in bits(exc[i]):
                blocked |= reverse[j]
        available = ((1 << n) - 1) & ~selected & ~blocked
        orders = [sorted(range(n), key=lambda i: Fraction(values[i], costs[i][k]) if costs[i][k] else Fraction(values[i] * (1 + sum(abs(v) for v in values)), 1), reverse=True) for k in range(d)]
        def search(m, avail, v, c):
            nonlocal best
            item = (v, c, m)
            if rank(item) < rank(best):
                best = item
            if not avail:
                return
            upper = v + sum(max(0, values[i]) for i in bits(avail))
            # A partition into conflict cliques gives another admissible bound.
            cliques = []
            for j in sorted(bits(avail), key=lambda j: values[j], reverse=True):
                if values[j] <= 0:
                    continue
                for q, (mask, maximum) in enumerate(cliques):
                    if exc[j] & mask == mask:
                        cliques[q] = (mask | 1 << j, maximum)
                        break
                else:
                    cliques.append((1 << j, values[j]))
            upper = min(upper, v + sum(maximum for _, maximum in cliques))
            for k in range(d):
                room = budget[k] - c[k]
                bound = Fraction(v)
                for i in orders[k]:
                    if not avail >> i & 1 or values[i] <= 0:
                        continue
                    weight = costs[i][k]
                    if weight <= room:
                        bound += values[i]; room -= weight
                    else:
                        bound += Fraction(values[i] * room, weight)
                        break
                upper = min(upper, bound)
            if upper < best[0] or (upper == best[0] and c > best[1]):
                return
            if upper == best[0] and c == best[1]:
                optimistic = m
                if m:
                    last = max(names(m))
                    for j in bits(avail):
                        if ids[j] < last:
                            optimistic |= 1 << j
                if names(optimistic) >= names(best[2]):
                    return
            i = max(bits(avail), key=lambda j: (values[j] > 0, (exc[j] & avail).bit_count() + (reverse[j] & avail).bit_count(), values[j]))
            add = closure[i] & ~m
            if add & ~avail == 0:
                nv, dc = totals(add)
                nc = tuple(c[k] + dc[k] for k in range(d))
                nm = m | add
                if all(nc[k] <= budget[k] for k in range(d)) and not any(exc[j] & nm for j in bits(add)):
                    ban = add
                    for j in bits(add):
                        for t in bits(exc[j]):
                            ban |= reverse[t]
                    search(nm, avail & ~ban, v + nv, nc)
            search(m, avail & ~reverse[i], v, c)
        search(selected, available, v0, c0)
    v, c, m = best
    return {'selected': list(names(m)), 'value': v, 'cost': list(c)}
