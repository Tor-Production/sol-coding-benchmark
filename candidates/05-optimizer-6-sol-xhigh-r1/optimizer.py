"""Exact constrained portfolio optimization."""

from bisect import bisect_left, bisect_right
from functools import cmp_to_key


def _lex_lt(a, b):
    """Lexicographic comparison of sorted index tuples encoded as masks."""
    if a == b:
        return False
    while a and b:
        x, y = a & -a, b & -b
        if x != y:
            return x < y
        a -= x
        b -= y
    return not a and bool(b)


def _validate(projects, budget, required):
    if type(projects) is not list or len(projects) > 32:
        raise ValueError("invalid projects")
    if type(budget) is not list or not 1 <= len(budget) <= 3 or any(
            type(x) is not int or x < 0 for x in budget):
        raise ValueError("invalid budget")
    if type(required) not in (list, tuple):
        raise ValueError("invalid required")
    keys = {"id", "value", "cost", "requires", "excludes"}
    seen = set()
    for p in projects:
        if type(p) is not dict or set(p) != keys:
            raise ValueError("invalid project")
        name = p["id"]
        if type(name) is not str or not name or name in seen:
            raise ValueError("invalid project ID")
        seen.add(name)
        if type(p["value"]) is not int:
            raise ValueError("invalid value")
        if type(p["cost"]) is not list or len(p["cost"]) != len(budget) or any(
                type(x) is not int or x < 0 for x in p["cost"]):
            raise ValueError("invalid cost")
        for field in ("requires", "excludes"):
            refs = p[field]
            if type(refs) is not list or any(type(x) is not str for x in refs) or len(set(refs)) != len(refs):
                raise ValueError("invalid reference list")
    if any(type(x) is not str for x in required) or len(set(required)) != len(required) or any(
            x not in seen for x in required):
        raise ValueError("invalid required ID")
    ordered = sorted(projects, key=lambda p: p["id"])
    ids = [p["id"] for p in ordered]
    positions = {name: i for i, name in enumerate(ids)}
    deps = []
    conflicts = [0] * len(ids)
    for i, p in enumerate(ordered):
        if any(x not in positions or x == ids[i] for x in p["requires"] + p["excludes"]):
            raise ValueError("unknown or self reference")
        deps.append(sum(1 << positions[x] for x in p["requires"]))
        for x in p["excludes"]:
            j = positions[x]
            conflicts[i] |= 1 << j
            conflicts[j] |= 1 << i
    color = [0] * len(ids)
    closure = [0] * len(ids)

    def visit(i):
        if color[i] == 1:
            raise ValueError("dependency cycle")
        if color[i] == 2:
            return closure[i]
        color[i] = 1
        result = 1 << i
        bits = deps[i]
        while bits:
            bit = bits & -bits
            result |= visit(bit.bit_length() - 1)
            bits -= bit
        color[i] = 2
        closure[i] = result
        return result

    for i in range(len(ids)):
        visit(i)
    mandatory = 0
    for name in required:
        mandatory |= closure[positions[name]]
    return ordered, ids, deps, conflicts, closure, mandatory


def _independent(ordered, budget, mandatory):
    """Meet in the middle, with exact multidimensional range maxima."""
    n, dims = len(ordered), len(budget)
    values = [p["value"] for p in ordered]
    prices = [p["cost"] for p in ordered]
    base_cost = tuple(sum(prices[i][d] for i in range(n) if mandatory & (1 << i))
                      for d in range(dims))
    if any(base_cost[d] > budget[d] for d in range(dims)):
        return None
    base_value = sum(values[i] for i in range(n) if mandatory & (1 << i))
    limits = tuple(budget[d] - base_cost[d] for d in range(dims))
    optional = [i for i in range(n) if not mandatory & (1 << i)]

    def enumerate_half(indices):
        rows = [(tuple(0 for _ in range(dims)), 0, 0)]
        for i in indices:
            extra = []
            for old_cost, old_value, old_mask in rows:
                cost = tuple(old_cost[d] + prices[i][d] for d in range(dims))
                if all(cost[d] <= limits[d] for d in range(dims)):
                    extra.append((cost, old_value + values[i], old_mask | (1 << i)))
            rows.extend(extra)
        return rows

    middle = len(optional) // 2
    left = enumerate_half(optional[:middle])
    right_rows = enumerate_half(optional[middle:])
    unique = {}
    for row in right_rows:
        cost, value, mask = row
        old = unique.get(cost)
        if old is None or value > old[1] or (value == old[1] and
                                             _lex_lt(mask | mandatory, old[2] | mandatory)):
            unique[cost] = row
    right = list(unique.values())

    def better(a, b):
        if b is None:
            return True
        ca, va, ma = right[a]
        cb, vb, mb = right[b]
        return va > vb or (va == vb and (ca < cb or
               (ca == cb and _lex_lt(ma | mandatory, mb | mandatory))))

    if dims == 1:
        right.sort(key=lambda row: row[0])
        positions = [row[0][0] for row in right]
        prefix = []
        champion = None
        for i in range(len(right)):
            if better(i, champion):
                champion = i
            prefix.append(champion)

        def query(lim):
            j = bisect_right(positions, lim[0]) - 1
            return prefix[j] if j >= 0 else None
    else:
        # Sweep the first coordinate. A Fenwick tree answers the remaining
        # one- or two-dimensional prefix maximum queries exactly.
        answers = [None] * len(left)
        right_order = sorted(range(len(right)), key=lambda i: right[i][0][0])
        left_order = sorted(range(len(left)), key=lambda i: limits[0] - left[i][0][0])
        xs = sorted({row[0][1] for row in right})
        size = len(xs)
        if dims == 2:
            tree = [None] * (size + 1)

            def update(i):
                x = bisect_left(xs, right[i][0][1]) + 1
                while x <= size:
                    if better(i, tree[x]):
                        tree[x] = i
                    x += x & -x

            def query(lim):
                x = bisect_right(xs, lim[1])
                champion = None
                while x:
                    if tree[x] is not None and better(tree[x], champion):
                        champion = tree[x]
                    x -= x & -x
                return champion
        else:
            ys = [[] for _ in range(size + 1)]
            for row in right:
                x = bisect_left(xs, row[0][1]) + 1
                while x <= size:
                    ys[x].append(row[0][2])
                    x += x & -x
            ys = [sorted(set(y)) for y in ys]
            tree = [[None] * (len(y) + 1) for y in ys]

            def update(i):
                x = bisect_left(xs, right[i][0][1]) + 1
                yval = right[i][0][2]
                while x <= size:
                    y = bisect_left(ys[x], yval) + 1
                    bucket = tree[x]
                    while y < len(bucket):
                        if better(i, bucket[y]):
                            bucket[y] = i
                        y += y & -y
                    x += x & -x

            def query(lim):
                x = bisect_right(xs, lim[1])
                champion = None
                while x:
                    y = bisect_right(ys[x], lim[2])
                    bucket = tree[x]
                    while y:
                        if bucket[y] is not None and better(bucket[y], champion):
                            champion = bucket[y]
                        y -= y & -y
                    x -= x & -x
                return champion

        rpos = 0
        for li in left_order:
            remaining = tuple(limits[d] - left[li][0][d] for d in range(dims))
            while rpos < len(right_order) and right[right_order[rpos]][0][0] <= remaining[0]:
                update(right_order[rpos])
                rpos += 1
            answers[li] = query(remaining)

    best_value = best_cost = best_mask = None
    for li, (lcost, lvalue, lmask) in enumerate(left):
        ri = (query(tuple(limits[d] - lcost[d] for d in range(dims)))
              if dims == 1 else answers[li])
        if ri is None:
            continue
        rcost, rvalue, rmask = right[ri]
        value = base_value + lvalue + rvalue
        cost = tuple(base_cost[d] + lcost[d] + rcost[d] for d in range(dims))
        mask = mandatory | lmask | rmask
        if (best_value is None or value > best_value or
                (value == best_value and (cost < best_cost or
                 (cost == best_cost and _lex_lt(mask, best_mask))))):
            best_mask, best_value, best_cost = mask, value, cost
    return best_mask, best_value, best_cost


def _constrained(ordered, budget, conflicts, closure, mandatory):
    n, dims = len(ordered), len(budget)
    all_bits = (1 << n) - 1
    values = [p["value"] for p in ordered]
    prices = [p["cost"] for p in ordered]
    dependents = [0] * n
    for i, mask in enumerate(closure):
        bits = mask
        while bits:
            bit = bits & -bits
            dependents[bit.bit_length() - 1] |= 1 << i
            bits -= bit
    blocked_by = [0] * n
    impossible = 0
    for i, mask in enumerate(closure):
        bits = mask
        while bits:
            bit = bits & -bits
            j = bit.bit_length() - 1
            if conflicts[j] & mask:
                impossible |= dependents[i]
            bits -= bit
        if any(sum(prices[j][d] for j in range(n) if mask & (1 << j)) > budget[d]
               for d in range(dims)):
            impossible |= dependents[i]
        bits = conflicts[i]
        while bits:
            bit = bits & -bits
            blocked_by[i] |= dependents[bit.bit_length() - 1]
            bits -= bit
    if mandatory & impossible:
        return None
    start_cost = tuple(sum(prices[i][d] for i in range(n) if mandatory & (1 << i))
                       for d in range(dims))
    if any(start_cost[d] > budget[d] for d in range(dims)):
        return None
    start_value = sum(values[i] for i in range(n) if mandatory & (1 << i))
    blocked = impossible
    bits = mandatory
    while bits:
        bit = bits & -bits
        blocked |= blocked_by[bit.bit_length() - 1]
        bits -= bit
    if blocked & mandatory:
        return None

    best_mask, best_value, best_cost = mandatory, start_value, start_cost

    def ratio_cmp(d):
        def compare(i, j):
            a, b = prices[i][d], prices[j][d]
            if a == 0 or b == 0:
                if a == b:
                    return 0
                return -1 if a == 0 else 1
            cross = values[i] * b - values[j] * a
            return -1 if cross > 0 else (1 if cross < 0 else 0)
        return compare

    ratio_order = [sorted(range(n), key=cmp_to_key(ratio_cmp(d))) for d in range(dims)]
    def cost_ratio_cmp(d):
        def compare(i, j):
            cross = prices[i][d] * values[j] - prices[j][d] * values[i]
            return -1 if cross < 0 else (1 if cross > 0 else 0)
        return compare

    cheap_order = [sorted((i for i in range(n) if values[i] > 0),
                          key=cmp_to_key(cost_ratio_cmp(d))) for d in range(dims)]
    branch_order = sorted(range(n), key=lambda i: (-values[i], -conflicts[i].bit_count(), i))

    def consider(mask, value, cost):
        nonlocal best_mask, best_value, best_cost
        if value > best_value or (value == best_value and
                (cost < best_cost or (cost == best_cost and _lex_lt(mask, best_mask)))):
            best_mask, best_value, best_cost = mask, value, cost

    def search(selected, excluded, value, cost):
        remaining = all_bits & ~(selected | excluded)
        candidates = 0
        bits = remaining
        while bits:
            bit = bits & -bits
            i = bit.bit_length() - 1
            if not closure[i] & excluded:
                candidates |= bit
            bits -= bit
        if not candidates:
            consider(selected, value, cost)
            return
        upper = value + sum(max(values[i], 0) for i in range(n) if candidates & (1 << i))
        if upper < best_value:
            return
        for d in range(dims):
            room = budget[d] - cost[d]
            bound = value
            for i in ratio_order[d]:
                if not candidates & (1 << i) or values[i] <= 0:
                    continue
                price = prices[i][d]
                if price <= room:
                    bound += values[i]
                    room -= price
                else:
                    bound += values[i] * room // price
                    break
            upper = min(upper, bound)
            if upper < best_value:
                return
        if upper == best_value:
            if cost > best_cost:
                return
            target = best_value - value
            lower_cost = []
            for d in range(dims):
                need = target
                price_bound = cost[d]
                for j in cheap_order[d]:
                    if not candidates & (1 << j):
                        continue
                    if need <= 0:
                        break
                    amount = min(need, values[j])
                    price_bound += (prices[j][d] * amount + values[j] - 1) // values[j]
                    need -= amount
                lower_cost.append(price_bound)
            lower_cost = tuple(lower_cost)
            if lower_cost > best_cost:
                return
            if lower_cost == best_cost:
                suffix = [0] * (n + 1)
                for j in range(n - 1, -1, -1):
                    suffix[j] = suffix[j + 1] + (values[j] if selected & (1 << j)
                                                else max(values[j], 0) if candidates & (1 << j)
                                                else 0)
                earliest = 0
                prefix_value = 0
                for j in range(n):
                    bit = 1 << j
                    if prefix_value >= best_value and not selected & (all_bits ^ (bit - 1)):
                        break
                    if selected & bit:
                        earliest |= bit
                        prefix_value += values[j]
                    elif candidates & bit and prefix_value + values[j] + suffix[j + 1] >= best_value:
                        earliest |= bit
                        prefix_value += values[j]
                if not _lex_lt(earliest, best_mask):
                    return
        consider(selected, value, cost)
        i = next(i for i in branch_order if candidates & (1 << i))
        addition = closure[i] & ~selected
        if not addition & excluded:
            new_cost = tuple(cost[d] + sum(prices[j][d] for j in range(n) if addition & (1 << j))
                             for d in range(dims))
            if all(new_cost[d] <= budget[d] for d in range(dims)):
                new_value = value + sum(values[j] for j in range(n) if addition & (1 << j))
                new_excluded = excluded
                bits = addition
                while bits:
                    bit = bits & -bits
                    new_excluded |= blocked_by[bit.bit_length() - 1]
                    bits -= bit
                if not new_excluded & (selected | addition):
                    search(selected | addition, new_excluded, new_value, new_cost)
        search(selected, excluded | dependents[i], value, cost)

    search(mandatory, blocked, start_value, start_cost)
    return best_mask, best_value, best_cost


def solve(projects, budget, required=()):
    """Return the globally best feasible portfolio, or None."""
    ordered, ids, deps, conflicts, closure, mandatory = _validate(projects, budget, required)
    if not any(deps) and not any(conflicts):
        result = _independent(ordered, budget, mandatory)
    else:
        result = _constrained(ordered, budget, conflicts, closure, mandatory)
    if result is None:
        return None
    mask, value, cost = result
    return {"selected": [ids[i] for i in range(len(ids)) if mask & (1 << i)],
            "value": value, "cost": list(cost)}
