"""Exact constrained portfolio optimization."""

from bisect import bisect_left, bisect_right
from fractions import Fraction


def solve(projects, budget, required=()):
    """Find the maximum-value feasible subset with the specified tie breaks."""
    if not isinstance(projects, list) or len(projects) > 32:
        raise ValueError("invalid projects")
    if (not isinstance(budget, list) or not 1 <= len(budget) <= 3
            or any(type(x) is not int or x < 0 for x in budget)):
        raise ValueError("invalid budget")
    if not isinstance(required, (list, tuple)):
        raise ValueError("invalid required IDs")
    fields = {"id", "value", "cost", "requires", "excludes"}
    source = {}
    for p in projects:
        if not isinstance(p, dict) or set(p) != fields:
            raise ValueError("invalid project fields")
        name = p["id"]
        if not isinstance(name, str) or not name or name in source:
            raise ValueError("invalid project ID")
        if type(p["value"]) is not int:
            raise ValueError("invalid project value")
        c = p["cost"]
        if (not isinstance(c, list) or len(c) != len(budget)
                or any(type(x) is not int or x < 0 for x in c)):
            raise ValueError("invalid project cost")
        for field in ("requires", "excludes"):
            refs = p[field]
            if (not isinstance(refs, list)
                    or any(not isinstance(x, str) or not x for x in refs)
                    or len(refs) != len(set(refs)) or name in refs):
                raise ValueError("invalid references")
        source[name] = p
    names = sorted(source)
    index = {name: i for i, name in enumerate(names)}
    if (any(not isinstance(x, str) or x not in index for x in required)
            or len(required) != len(set(required))):
        raise ValueError("invalid required IDs")
    n, dims = len(names), len(budget)
    values = [source[name]["value"] for name in names]
    costs = [tuple(source[name]["cost"]) for name in names]
    deps, conflicts = [0] * n, [0] * n
    for i, name in enumerate(names):
        for ref in source[name]["requires"]:
            if ref not in index:
                raise ValueError("unknown dependency")
            deps[i] |= 1 << index[ref]
        for ref in source[name]["excludes"]:
            if ref not in index:
                raise ValueError("unknown exclusion")
            j = index[ref]
            conflicts[i] |= 1 << j
            conflicts[j] |= 1 << i

    closure, state = [0] * n, [0] * n

    def visit(i):
        if state[i] == 1:
            raise ValueError("dependency cycle")
        if state[i] == 2:
            return closure[i]
        state[i] = 1
        bits, result = deps[i], 1 << i
        while bits:
            bit = bits & -bits
            result |= visit(bit.bit_length() - 1)
            bits -= bit
        state[i], closure[i] = 2, result
        return result

    for i in range(n):
        visit(i)
    mandatory = 0
    for name in required:
        mandatory |= closure[index[name]]

    def measure(mask):
        value, total = 0, [0] * dims
        while mask:
            bit = mask & -mask
            i = bit.bit_length() - 1
            value += values[i]
            for d in range(dims):
                total[d] += costs[i][d]
            mask -= bit
        return value, tuple(total)

    def feasible(mask, total):
        if any(total[d] > budget[d] for d in range(dims)):
            return False
        bits = mask
        while bits:
            bit = bits & -bits
            if conflicts[bit.bit_length() - 1] & mask:
                return False
            bits -= bit
        return True

    base_value, base_cost = measure(mandatory)
    if not feasible(mandatory, base_cost):
        return None

    def id_tuple(mask):
        return tuple(names[i] for i in range(n) if mask & (1 << i))

    best_mask, best_value, best_cost = mandatory, base_value, base_cost

    def consider(mask, value, total):
        nonlocal best_mask, best_value, best_cost
        if (value > best_value or
                (value == best_value and
                 (total < best_cost or
                  (total == best_cost and id_tuple(mask) < id_tuple(best_mask))))):
            best_mask, best_value, best_cost = mask, value, total

    optional = [i for i in range(n) if not mandatory & (1 << i)]
    links = [deps[i] | conflicts[i] for i in range(n)]
    safe_cuts = []
    for cut in range(n + 1):
        low = (1 << cut) - 1
        if all(not (links[i] & (~low if i < cut else low))
               for i in range(n)):
            left_count = sum(i < cut for i in optional)
            if max(left_count, len(optional) - left_count) <= 17:
                safe_cuts.append((max(left_count, len(optional) - left_count), cut))
    if safe_cuts:
        split = min(safe_cuts)[1]
        linked = any(links)

        def half_states(items):
            states = [(0, (0,) * dims, 0)]
            for i in items:
                extra = []
                for value, total, mask in states:
                    new_total = tuple(total[d] + costs[i][d] for d in range(dims))
                    if all(new_total[d] <= budget[d] for d in range(dims)):
                        extra.append((value + values[i], new_total, mask | (1 << i)))
                states.extend(extra)
            if linked:
                valid = []
                for value, total, mask in states:
                    full = mask | mandatory
                    bits = mask
                    while bits:
                        bit = bits & -bits
                        if closure[bit.bit_length() - 1] & ~full:
                            break
                        bits -= bit
                    else:
                        if feasible(full, tuple(total[d] + base_cost[d]
                                                for d in range(dims))):
                            valid.append((value, total, mask))
                states = valid
            return states

        left = half_states([i for i in optional if i < split])
        right = half_states([i for i in optional if i >= split])
        right.sort(key=lambda row: row[1][0])
        left.sort(key=lambda row: budget[0] - base_cost[0] - row[1][0])

        def better_right(a, b):
            if b is None:
                return True
            if a[0] != b[0]:
                return a[0] > b[0]
            if a[1] != b[1]:
                return a[1] < b[1]
            return id_tuple(a[2] | mandatory) < id_tuple(b[2] | mandatory)

        if dims == 1:
            current = None

            def insert(row):
                nonlocal current
                if better_right(row, current):
                    current = row

            def query(limits):
                return current

        elif dims == 2:
            ys = sorted({row[1][1] for row in right})
            tree = [None] * (len(ys) + 1)

            def insert(row):
                k = bisect_left(ys, row[1][1]) + 1
                while k < len(tree):
                    if better_right(row, tree[k]):
                        tree[k] = row
                    k += k & -k

            def query(limits):
                k, answer = bisect_right(ys, limits[1]), None
                while k:
                    if tree[k] is not None and better_right(tree[k], answer):
                        answer = tree[k]
                    k -= k & -k
                return answer

        else:
            ys = sorted({row[1][1] for row in right})
            zs = [[] for _ in range(len(ys) + 1)]
            for row in right:
                k = bisect_left(ys, row[1][1]) + 1
                while k < len(zs):
                    zs[k].append(row[1][2])
                    k += k & -k
            zs = [sorted(set(z)) for z in zs]
            tree = [[None] * (len(z) + 1) for z in zs]

            def insert(row):
                k = bisect_left(ys, row[1][1]) + 1
                while k < len(zs):
                    j = bisect_left(zs[k], row[1][2]) + 1
                    while j < len(tree[k]):
                        if better_right(row, tree[k][j]):
                            tree[k][j] = row
                        j += j & -j
                    k += k & -k

            def query(limits):
                k, answer = bisect_right(ys, limits[1]), None
                while k:
                    j = bisect_right(zs[k], limits[2])
                    while j:
                        if tree[k][j] is not None and better_right(tree[k][j], answer):
                            answer = tree[k][j]
                        j -= j & -j
                    k -= k & -k
                return answer

        pos = 0
        for lv, lc, lm in left:
            limits = tuple(budget[d] - base_cost[d] - lc[d] for d in range(dims))
            if any(x < 0 for x in limits):
                continue
            while pos < len(right) and right[pos][1][0] <= limits[0]:
                insert(right[pos])
                pos += 1
            row = query(limits)
            if row is not None:
                total = tuple(base_cost[d] + lc[d] + row[1][d]
                              for d in range(dims))
                consider(mandatory | lm | row[2], base_value + lv + row[0], total)
    else:
        reverse = [0] * n
        for i, bits in enumerate(closure):
            while bits:
                bit = bits & -bits
                reverse[bit.bit_length() - 1] |= 1 << i
                bits -= bit

        def lift(bits, masks):
            result = 0
            while bits:
                bit = bits & -bits
                result |= masks[bit.bit_length() - 1]
                bits -= bit
            return result

        def ban(bits):
            return lift(lift(bits, conflicts), reverse)

        order = sorted(range(n), key=lambda i: (
            Fraction(values[i], 1 + sum(costs[i])), values[i],
            (conflicts[i] | reverse[i]).bit_count()), reverse=True)
        frac_order = [sorted(
            (i for i in range(n) if values[i] > 0 and costs[i][d] > 0),
            key=lambda i: Fraction(values[i], costs[i][d]), reverse=True)
            for d in range(dims)]
        seen = set()
        all_bits = (1 << n) - 1

        def search(chosen, banned, value, total):
            remaining = all_bits & ~(chosen | banned)
            key = (chosen, banned)
            if key in seen:
                return
            seen.add(key)
            consider(chosen, value, total)
            if not remaining:
                return
            upper = value + sum(values[i] for i in range(n)
                                if remaining & (1 << i) and values[i] > 0)
            if upper < best_value:
                return
            for d in range(dims):
                capacity = budget[d] - total[d]
                bound = value + sum(values[i] for i in range(n)
                                    if remaining & (1 << i) and values[i] > 0
                                    and costs[i][d] == 0)
                for i in frac_order[d]:
                    if not remaining & (1 << i):
                        continue
                    amount = costs[i][d]
                    if amount <= capacity:
                        capacity -= amount
                        bound += values[i]
                    else:
                        bound += (values[i] * capacity + amount - 1) // amount
                        break
                upper = min(upper, bound)
                if upper < best_value:
                    return
            if upper == best_value and total > best_cost:
                return
            i = next(i for i in order if remaining & (1 << i))
            addition = closure[i] & ~chosen
            if not addition & banned:
                increment, extra = measure(addition)
                new_total = tuple(total[d] + extra[d] for d in range(dims))
                new_chosen = chosen | addition
                if feasible(new_chosen, new_total):
                    new_banned = banned | ban(addition)
                    if not new_banned & new_chosen:
                        search(new_chosen, new_banned, value + increment, new_total)
            search(chosen, banned | reverse[i], value, total)

        search(mandatory, ban(mandatory), base_value, base_cost)

    return {"selected": list(id_tuple(best_mask)), "value": best_value,
            "cost": list(best_cost)}
