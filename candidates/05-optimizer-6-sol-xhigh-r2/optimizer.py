"""Exact optimization of small portfolios with dependencies and exclusions."""

from functools import cmp_to_key


def solve(projects, budget, required=()):
    """Return the best feasible portfolio, or None if mandatory work cannot fit."""
    if not isinstance(projects, list) or len(projects) > 32:
        raise ValueError("projects must be a list of at most 32 dictionaries")
    if not isinstance(budget, list) or not 1 <= len(budget) <= 3:
        raise ValueError("budget must be a list of one to three integers")
    if any(type(x) is not int or x < 0 for x in budget):
        raise ValueError("invalid budget")
    if not isinstance(required, (list, tuple)):
        raise ValueError("required must be a list or tuple")

    dimensions = len(budget)
    by_id = {}
    expected_keys = {"id", "value", "cost", "requires", "excludes"}
    for project in projects:
        if not isinstance(project, dict) or set(project) != expected_keys:
            raise ValueError("invalid project dictionary")
        name = project["id"]
        if not isinstance(name, str) or not name or name in by_id:
            raise ValueError("invalid or duplicate project ID")
        if type(project["value"]) is not int:
            raise ValueError("project value must be an integer")
        costs = project["cost"]
        if (not isinstance(costs, list) or len(costs) != dimensions
                or any(type(x) is not int or x < 0 for x in costs)):
            raise ValueError("invalid project cost")
        for field in ("requires", "excludes"):
            refs = project[field]
            if not isinstance(refs, list) or any(
                not isinstance(ref, str) or not ref or ref == name for ref in refs
            ) or len(refs) != len(set(refs)):
                raise ValueError("invalid project references")
        by_id[name] = project

    names = sorted(by_id)
    index = {name: i for i, name in enumerate(names)}
    for project in projects:
        if any(ref not in index for ref in project["requires"] + project["excludes"]):
            raise ValueError("unknown project reference")
    if any(not isinstance(name, str) or name not in index for name in required):
        raise ValueError("unknown required project")
    if len(required) != len(set(required)):
        raise ValueError("duplicate required project")

    n = len(names)
    all_bits = (1 << n) - 1
    bits = [1 << i for i in range(n)]
    values = [by_id[name]["value"] for name in names]
    costs = [tuple(by_id[name]["cost"]) for name in names]
    direct = [0] * n
    excludes = [0] * n
    for i, name in enumerate(names):
        for ref in by_id[name]["requires"]:
            direct[i] |= bits[index[ref]]
        for ref in by_id[name]["excludes"]:
            j = index[ref]
            excludes[i] |= bits[j]
            excludes[j] |= bits[i]

    # A three-color DFS validates cycles, including projects too costly to use.
    color = [0] * n
    closure = [0] * n

    def visit(i):
        if color[i] == 1:
            raise ValueError("dependency cycle")
        if color[i] == 2:
            return closure[i]
        color[i] = 1
        result = bits[i]
        pending = direct[i]
        while pending:
            bit = pending & -pending
            pending -= bit
            result |= visit(bit.bit_length() - 1)
        color[i] = 2
        closure[i] = result
        return result

    for i in range(n):
        visit(i)

    # If i is banned, every project depending on i is also banned.
    dependents = [0] * n
    for i in range(n):
        pending = closure[i]
        while pending:
            bit = pending & -pending
            pending -= bit
            dependents[bit.bit_length() - 1] |= bits[i]

    def expand_bans(mask):
        expanded = 0
        while mask:
            bit = mask & -mask
            mask -= bit
            expanded |= dependents[bit.bit_length() - 1]
        return expanded

    def exclusions_of(mask):
        result = 0
        while mask:
            bit = mask & -mask
            mask -= bit
            result |= excludes[bit.bit_length() - 1]
        return result

    def totals(mask):
        v = 0
        c = [0] * dimensions
        while mask:
            bit = mask & -mask
            mask -= bit
            i = bit.bit_length() - 1
            v += values[i]
            for d in range(dimensions):
                c[d] += costs[i][d]
        return v, tuple(c)

    # Discard selections that cannot fit even alone with their prerequisites.
    unusable = 0
    for i in range(n):
        members = closure[i]
        _, package_cost = totals(members)
        if (any(package_cost[d] > budget[d] for d in range(dimensions))
                or exclusions_of(members) & members):
            unusable |= bits[i]
    banned = expand_bans(unusable)

    selected = 0
    for name in required:
        selected |= closure[index[name]]
    if selected & banned or exclusions_of(selected) & selected:
        return None
    initial_value, initial_cost = totals(selected)
    if any(initial_cost[d] > budget[d] for d in range(dimensions)):
        return None
    banned |= expand_bans(exclusions_of(selected))
    if selected & banned:
        return None

    # Two projects whose dependency closures cannot coexist are incompatible.
    incompatible = excludes[:]
    for i in range(n):
        for j in range(i + 1, n):
            union = closure[i] | closure[j]
            if exclusions_of(union) & union:
                incompatible[i] |= bits[j]
                incompatible[j] |= bits[i]
                continue
            _, pair_cost = totals(union)
            if any(pair_cost[d] > budget[d] for d in range(dimensions)):
                incompatible[i] |= bits[j]
                incompatible[j] |= bits[i]

    positive = [i for i in range(n) if values[i] > 0]
    by_value = sorted(positive, key=lambda i: values[i], reverse=True)
    ratio_orders = []
    cheap_orders = []
    for d in range(dimensions):
        paid = [i for i in positive if costs[i][d]]

        def value_per_cost(i, j, dimension=d):
            left = values[i] * costs[j][dimension]
            right = values[j] * costs[i][dimension]
            return (right > left) - (right < left)

        ratio_orders.append(sorted(paid, key=cmp_to_key(value_per_cost)))

        def cost_per_value(i, j, dimension=d):
            left = costs[i][dimension] * values[j]
            right = costs[j][dimension] * values[i]
            return (left > right) - (left < right)

        cheap_orders.append(sorted(positive, key=cmp_to_key(cost_per_value)))

    best_mask = selected
    best_value = initial_value
    best_cost = initial_cost
    best_names = tuple(names[i] for i in range(n) if selected & bits[i])

    def maybe_record(mask, value, total_cost):
        nonlocal best_mask, best_value, best_cost, best_names
        if value < best_value or (value == best_value and total_cost > best_cost):
            return
        candidate_names = tuple(names[i] for i in range(n) if mask & bits[i])
        if (value > best_value or total_cost < best_cost
                or candidate_names < best_names):
            best_mask, best_value, best_cost, best_names = (
                mask, value, total_cost, candidate_names
            )

    def upper_bound(available, value, total_cost):
        """Integer floor of admissible value bounds, ignoring constraints."""
        bound = value
        for i in positive:
            if available & bits[i]:
                bound += values[i]
        if bound <= best_value:
            return bound

        # In each dimension, relax every project to a divisible item.
        for d in range(dimensions):
            capacity = budget[d] - total_cost[d]
            possible = value
            for i in positive:
                if available & bits[i] and costs[i][d] == 0:
                    possible += values[i]
            for i in ratio_orders[d]:
                if not available & bits[i]:
                    continue
                item_cost = costs[i][d]
                if item_cost <= capacity:
                    possible += values[i]
                    capacity -= item_cost
                else:
                    possible += capacity * values[i] // item_cost
                    break
            bound = min(bound, possible)
            if bound < best_value:
                break
        return bound

    def minimum_costs(available, value, total_cost):
        """Optimistic per-dimension cost to reach the incumbent value."""
        target = best_value - value
        result = []
        for d in range(dimensions):
            needed = target
            additional = 0
            for i in cheap_orders[d]:
                if not available & bits[i]:
                    continue
                take = min(needed, values[i])
                additional += (take * costs[i][d] + values[i] - 1) // values[i]
                needed -= take
                if needed <= 0:
                    break
            result.append(total_cost[d] + additional)
        return tuple(result)

    def optimistic_names(mask, available, value):
        """Smallest possible ID tuple after relaxing costs and constraints.

        An extension needs at least ``best_value - value`` positive value.
        Before it can stop, selecting each available earlier ID gives the
        lexicographically smallest tuple, even when that ID has no value.
        """
        needed = best_value - value
        last_forced = mask.bit_length() - 1
        result = []
        for i in range(n):
            if i > last_forced and needed <= 0:
                break
            if mask & bits[i]:
                result.append(names[i])
            elif available & bits[i]:
                result.append(names[i])
                needed -= max(values[i], 0)
        return tuple(result)

    def search(mask, banned_mask, value, total_cost, next_index):
        maybe_record(mask, value, total_cost)
        while next_index < n and (mask | banned_mask) & bits[next_index]:
            next_index += 1
        if next_index == n:
            return
        available = all_bits & ~(mask | banned_mask)

        # Partition available projects into exclusion cliques. At most one
        # member of each clique can be selected.
        cliques = []
        clique_bound = value
        for i in by_value:
            if not available & bits[i]:
                continue
            for group_index, group in enumerate(cliques):
                if group & ~incompatible[i] == 0:
                    cliques[group_index] |= bits[i]
                    break
            else:
                cliques.append(bits[i])
                clique_bound += values[i]
        if clique_bound < best_value:
            return
        bound = min(clique_bound, upper_bound(available, value, total_cost))
        if bound < best_value:
            return
        if bound == best_value:
            lower_cost = minimum_costs(available, value, total_cost)
            if lower_cost > best_cost or (
                lower_cost == best_cost
                and optimistic_names(mask, available, value) >= best_names
            ):
                return

        i = next_index
        delta = closure[i] & ~mask
        if not delta & banned_mask:
            new_value = value
            new_cost = list(total_cost)
            pending = delta
            while pending:
                bit = pending & -pending
                pending -= bit
                j = bit.bit_length() - 1
                new_value += values[j]
                for d in range(dimensions):
                    new_cost[d] += costs[j][d]
            if all(new_cost[d] <= budget[d] for d in range(dimensions)):
                new_banned = banned_mask | expand_bans(exclusions_of(delta))
                if not (mask | delta) & new_banned:
                    search(mask | delta, new_banned, new_value,
                           tuple(new_cost), i + 1)

        search(mask, banned_mask | dependents[i], value, total_cost, i + 1)

    search(selected, banned, initial_value, initial_cost, 0)
    return {"selected": list(best_names), "value": best_value,
            "cost": list(best_cost)}
