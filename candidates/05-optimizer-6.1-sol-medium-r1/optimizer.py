"""Exact portfolio optimization using a bounded meet-in-the-middle search."""


def solve(projects, budget, required=()):
    """Validate the input and return the best feasible portfolio, or None."""
    def integer(x):
        return isinstance(x, int) and not isinstance(x, bool)

    if (not isinstance(budget, list) or not 1 <= len(budget) <= 3
            or any(not integer(x) or x < 0 for x in budget)):
        raise ValueError('invalid budget')
    if not isinstance(projects, list) or len(projects) > 32:
        raise ValueError('invalid projects')
    if not isinstance(required, (list, tuple)):
        raise ValueError('invalid required IDs')
    dimensions = len(budget)
    ids = set()
    for p in projects:
        if not isinstance(p, dict) or set(p) != {'id', 'value', 'cost', 'requires', 'excludes'}:
            raise ValueError('invalid project keys')
        name = p['id']
        if not isinstance(name, str) or not name or name in ids:
            raise ValueError('invalid project ID')
        ids.add(name)
        if not integer(p['value']):
            raise ValueError('invalid value')
        if (not isinstance(p['cost'], list) or len(p['cost']) != dimensions
                or any(not integer(x) or x < 0 for x in p['cost'])):
            raise ValueError('invalid cost')
    for p in projects:
        for field in ('requires', 'excludes'):
            refs = p[field]
            if (not isinstance(refs, list)
                    or any(not isinstance(x, str) or x not in ids or x == p['id'] for x in refs)
                    or len(set(refs)) != len(refs)):
                raise ValueError('invalid references')
    if (any(not isinstance(x, str) or x not in ids for x in required)
            or len(set(required)) != len(required)):
        raise ValueError('invalid required IDs')

    ordered = sorted(projects, key=lambda p: p['id'])
    names = [p['id'] for p in ordered]
    n = len(ordered)
    index = {name: i for i, name in enumerate(names)}
    needs = [sum(1 << index[x] for x in p['requires']) for p in ordered]
    bans = [0] * n
    for i, p in enumerate(ordered):
        for name in p['excludes']:
            j = index[name]
            bans[i] |= 1 << j
            bans[j] |= 1 << i
    closures = [0] * n
    visiting = [0] * n

    def closure(i):
        if visiting[i] == 1:
            raise ValueError('cyclic dependencies')
        if visiting[i] == 2:
            return closures[i]
        visiting[i] = 1
        mask = 1 << i
        pending = needs[i]
        while pending:
            bit = pending & -pending
            pending -= bit
            mask |= closure(bit.bit_length() - 1)
        closures[i] = mask
        visiting[i] = 2
        return mask

    for i in range(n):
        closure(i)
    mandatory = 0
    for name in required:
        mandatory |= closures[index[name]]
    costs = [tuple(p['cost']) for p in ordered]
    values = [p['value'] for p in ordered]
    forbidden = 0
    for i, mask in enumerate(closures):
        members = [j for j in range(n) if mask >> j & 1]
        if (any(bans[j] & mask for j in members)
                or any(sum(costs[j][d] for j in members) > budget[d]
                       for d in range(dimensions))):
            forbidden |= 1 << i
    if mandatory & forbidden:
        return None

    def enumerate_half(start, end):
        # Arrays include invalid intermediate masks: a later bit can supply
        # a missing dependency, so invalidity cannot prune this recurrence.
        size = 1 << (end - start)
        totals = [None] * size
        scores = [0] * size
        reqs = [0] * size
        conflicts = [0] * size
        totals[0] = (0,) * dimensions
        half = ((1 << end) - 1) ^ ((1 << start) - 1)
        outside = ((1 << n) - 1) ^ half
        must = mandatory & half
        rows = []
        for local in range(size):
            mask = local << start
            if local:
                bit = local & -local
                prev = local - bit
                i = start + bit.bit_length() - 1
                totals[local] = tuple(a + b for a, b in zip(totals[prev], costs[i]))
                scores[local] = scores[prev] + values[i]
                reqs[local] = reqs[prev] | needs[i]
                conflicts[local] = conflicts[prev] | bans[i]
            cost = totals[local]
            if (mask & forbidden or mask & must != must
                    or reqs[local] & half & ~mask or conflicts[local] & mask
                    or any(cost[d] > budget[d] for d in range(dimensions))):
                continue
            rows.append((cost, scores[local], mask,
                         reqs[local] & outside, conflicts[local] & outside))
        return rows

    split = n // 2
    left = enumerate_half(0, split)
    right = enumerate_half(split, n)
    if not left or not right:
        return None

    def selected(mask):
        return tuple(names[i] for i in range(n) if mask >> i & 1)

    def mask_less(a, b):
        """Compare sorted ID tuples without materializing them."""
        different = a ^ b
        if not different:
            return False
        bit = different & -different
        # The first differing ID decides unless one tuple ends there.
        return b >= bit if a & bit else a < bit

    # Right IDs follow every left ID in string order. For a fixed left
    # subset, comparing right ID tuples therefore gives the full tuple order.
    # Keep only the best representative with identical cross-half behavior
    # and costs. This also compresses large families of interchangeable items.
    relevant = 0
    for row in left:
        relevant |= row[3] | row[4]
    representatives = {}
    for row in right:
        c, v, mask, need, ban = row
        key = (c, need, ban, mask & relevant)
        previous = representatives.get(key)
        if (previous is None or v > previous[1]
                or (v == previous[1] and mask_less(mask, previous[2]))):
            representatives[key] = row
    right = list(representatives.values())

    class Node:
        __slots__ = ('lo', 'hi', 'value', 'cost', 'bestmask',
                     'need', 'ban', 'allowed', 'forced', 'children', 'rows')

        def __init__(self, rows):
            self.lo = tuple(min(r[0][d] for r in rows) for d in range(dimensions))
            self.hi = tuple(max(r[0][d] for r in rows) for d in range(dimensions))
            self.value = max(r[1] for r in rows)
            self.cost = min(r[0] for r in rows if r[1] == self.value)
            self.bestmask = None
            self.need = self.ban = (1 << n) - 1
            self.allowed = 0
            self.forced = (1 << n) - 1
            for c, v, mask, need, ban in rows:
                self.need &= need
                self.ban &= ban
                self.allowed |= mask
                self.forced &= mask
                if v == self.value and c == self.cost:
                    if self.bestmask is None or mask_less(mask, self.bestmask):
                        self.bestmask = mask
            self.children = self.rows = None
            if len(rows) <= 12:
                self.rows = rows
            else:
                d = max(range(dimensions), key=lambda d: self.hi[d] - self.lo[d])
                if self.hi[d] != self.lo[d]:
                    rows.sort(key=lambda r: r[0][d])
                else:
                    rows.sort(key=lambda r: r[2])
                mid = len(rows) // 2
                self.children = (Node(rows[:mid]), Node(rows[mid:]))

    tree = Node(right)
    right_indices = range(split, n)
    left_bits = (1 << split) - 1
    uniform = (split < n and values[split] > 0
               and all(values[i] == values[split] and costs[i] == costs[split]
                       for i in right_indices))
    best_value = None
    best_cost = best_ids = best_mask = None

    def consider(value, cost, mask):
        nonlocal best_value, best_cost, best_ids, best_mask
        if best_value is not None:
            if value < best_value or (value == best_value and cost > best_cost):
                return
        chosen = selected(mask)
        if (best_value is None or value > best_value or cost < best_cost
                or chosen < best_ids):
            best_value, best_cost, best_ids, best_mask = value, cost, chosen, mask

    # A high-value first pass supplies a useful incumbent early.
    left.sort(key=lambda r: (-r[1], r[0]))
    for lc, lv, lm, ln, lb in left:
        capacity = tuple(budget[d] - lc[d] for d in range(dimensions))
        if uniform:
            eligible = 0
            for i in right_indices:
                if not (needs[i] & left_bits & ~lm or bans[i] & lm
                        or forbidden & (1 << i)):
                    eligible |= 1 << i
            if ln & ~eligible:
                continue
        stack = [tree]
        while stack:
            node = stack.pop()
            if (node.need & ~lm or node.ban & lm
                    or ln & ~node.allowed or lb & node.forced
                    or any(node.lo[d] > capacity[d] for d in range(dimensions))):
                continue
            upper = lv + node.value
            if uniform:
                available = node.allowed & eligible
                upper = min(upper, lv + available.bit_count() * values[split])
            if best_value is not None:
                if upper < best_value:
                    continue
                if upper == best_value:
                    if uniform:
                        # Equal positive values fix the number of right
                        # projects needed to tie the incumbent. Relax their
                        # pairwise constraints to obtain cost and ID bounds.
                        count = (best_value - lv) // values[split]
                        lower_cost = tuple(lc[d] + max(node.lo[d], count * costs[split][d])
                                           for d in range(dimensions))
                    else:
                        lower_cost = tuple(lc[d] + node.cost[d] for d in range(dimensions))
                    if lower_cost > best_cost:
                        continue
                    if lower_cost == best_cost:
                        if uniform:
                            must = node.forced | ln
                            if must & ~available:
                                continue
                            optimistic = must | (available & ((1 << must.bit_length()) - 1))
                            rest = available & ~optimistic
                            while optimistic.bit_count() < count and rest:
                                bit = rest & -rest
                                optimistic |= bit
                                rest -= bit
                            optimistic |= lm
                        else:
                            optimistic = lm | node.bestmask
                        if not mask_less(optimistic, best_mask):
                            continue
            if node.rows is not None:
                for rc, rv, rm, rn, rb in node.rows:
                    if (ln & ~rm or lb & rm or rn & ~lm or rb & lm
                            or any(rc[d] > capacity[d] for d in range(dimensions))):
                        continue
                    consider(lv + rv, tuple(lc[d] + rc[d] for d in range(dimensions)), lm | rm)
            else:
                a, b = node.children
                if (a.value, tuple(-x for x in a.cost)) < (b.value, tuple(-x for x in b.cost)):
                    a, b = b, a
                stack.append(b)
                stack.append(a)
    if best_value is None:
        return None
    return {'selected': list(best_ids), 'value': best_value, 'cost': list(best_cost)}
