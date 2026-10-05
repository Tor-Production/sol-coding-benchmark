"""Exact portfolio optimization, with no third-party dependencies."""

from bisect import bisect_left, bisect_right
from functools import cmp_to_key


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _validate(projects, budget, required):
    if not isinstance(projects, list) or len(projects) > 32:
        raise ValueError("projects must be a list of at most 32 dictionaries")
    if (not isinstance(budget, list) or not 1 <= len(budget) <= 3
            or any(not _integer(c) or c < 0 for c in budget)):
        raise ValueError("budget must contain one to three nonnegative integers")
    if not isinstance(required, (list, tuple)):
        raise ValueError("required must be a list or tuple")

    by_id = {}
    keys = {"id", "value", "cost", "requires", "excludes"}
    for project in projects:
        if not isinstance(project, dict) or set(project) != keys:
            raise ValueError("project dictionaries must have exactly the specified keys")
        name = project["id"]
        if not isinstance(name, str) or not name or name in by_id:
            raise ValueError("project IDs must be unique nonempty strings")
        if not _integer(project["value"]):
            raise ValueError("project values must be integers, not bools")
        costs = project["cost"]
        if (not isinstance(costs, list) or len(costs) != len(budget)
                or any(not _integer(c) or c < 0 for c in costs)):
            raise ValueError("project costs must match the budget dimensions")
        for field in ("requires", "excludes"):
            refs = project[field]
            if not isinstance(refs, list):
                raise ValueError("references must be lists")
            seen = set()
            for ref in refs:
                if (not isinstance(ref, str) or not ref or ref == name
                        or ref in seen):
                    raise ValueError("references must be distinct other project IDs")
                seen.add(ref)
        by_id[name] = project

    for project in projects:
        for field in ("requires", "excludes"):
            if any(ref not in by_id for ref in project[field]):
                raise ValueError("unknown project reference")
    seen = set()
    for ref in required:
        if not isinstance(ref, str) or ref not in by_id or ref in seen:
            raise ValueError("required IDs must be distinct known strings")
        seen.add(ref)

    # High bits represent earlier IDs. Maximizing a mask prefers inclusion at
    # the first differing ID; solve() handles the tuple-prefix exception below.
    names = sorted(by_id, reverse=True)
    indices = {name: i for i, name in enumerate(names)}
    n = len(names)
    values = [by_id[name]["value"] for name in names]
    costs = [tuple(by_id[name]["cost"]) for name in names]
    direct = [0] * n
    exclusions = [0] * n
    for i, name in enumerate(names):
        for ref in by_id[name]["requires"]:
            direct[i] |= 1 << indices[ref]
        for ref in by_id[name]["excludes"]:
            j = indices[ref]
            exclusions[i] |= 1 << j
            exclusions[j] |= 1 << i

    state = [0] * n
    closures = [0] * n

    def visit(i):
        if state[i] == 1:
            raise ValueError("dependency graph must be acyclic")
        if state[i] == 2:
            return closures[i]
        state[i] = 1
        mask = 1 << i
        deps = direct[i]
        while deps:
            bit = deps & -deps
            deps -= bit
            mask |= visit(bit.bit_length() - 1)
        closures[i] = mask
        state[i] = 2
        return mask

    # Validate cycles throughout the graph, including unaffordable projects.
    for i in range(n):
        visit(i)
    forced = 0
    for name in required:
        forced |= closures[indices[name]]
    return names, values, costs, closures, exclusions, forced


def _rank(record):
    value, costs, mask = record
    return (value, *(-c for c in costs), mask)


def _combine_menus(menus, capacity):
    """Enumerate one half of independent components, pruning excess costs."""
    d = len(capacity)
    records = [(0, (0,) * d, 0)]
    for menu in menus:
        combined = []
        for value, costs, mask in records:
            for v, c, m in menu:
                totals = tuple(costs[j] + c[j] for j in range(d))
                if all(totals[j] <= capacity[j] for j in range(d)):
                    combined.append((value + v, totals, mask | m))
        records = combined
    return records


def _meet_in_middle(menus, capacity):
    """Exact additive optimization using offline orthant maximum queries.

    Each component menu contains every potentially optimal local selection.
    The two halves are joined by querying the best right-hand record whose
    costs fit the capacity left by a left-hand record. A sweep handles the
    first cost dimension; Fenwick trees handle the other zero, one, or two.
    """
    # If every component's unconstrained optimum fits simultaneously, their
    # sum is already optimal in every ranking field.
    local_best = [max(menu, key=_rank) for menu in menus]
    totals = tuple(sum(record[1][j] for record in local_best)
                   for j in range(len(capacity)))
    if all(totals[j] <= capacity[j] for j in range(len(capacity))):
        return (sum(record[0] for record in local_best), totals,
                sum(record[2] for record in local_best))

    halves = [[], []]
    sizes = [1, 1]
    for menu in sorted(menus, key=len, reverse=True):
        side = 0 if sizes[0] <= sizes[1] else 1
        halves[side].append(menu)
        sizes[side] *= len(menu)
    if max(sizes) > 131072:
        return None  # The caller uses branch-and-bound instead.

    left = _combine_menus(halves[0], capacity)
    right = _combine_menus(halves[1], capacity)
    d = len(capacity)
    if len(left) == 1 or len(right) == 1:
        if len(right) != 1:
            left, right = right, left
        v, c, m = right[0]
        best = None
        for av, ac, am in left:
            totals = tuple(ac[j] + c[j] for j in range(d))
            if all(totals[j] <= capacity[j] for j in range(d)):
                candidate = (av + v, totals, am | m)
                if best is None or _rank(candidate) > _rank(best):
                    best = candidate
        return best

    right.sort(key=lambda r: r[1][0])
    left.sort(key=lambda r: r[1][0], reverse=True)
    points = [(c, _rank((v, c, m))) for v, c, m in right]
    best = None
    cursor = 0

    def consider(record, winner):
        nonlocal best
        if winner is not None:
            v, c, m = record
            candidate = (v + winner[0],
                         *(winner[j + 1] - c[j] for j in range(d)),
                         m | winner[-1])
            if best is None or candidate > best:
                best = candidate

    if d == 1:
        winner = None
        for record in left:
            limit = capacity[0] - record[1][0]
            while cursor < len(points) and points[cursor][0][0] <= limit:
                score = points[cursor][1]
                if winner is None or score > winner:
                    winner = score
                cursor += 1
            consider(record, winner)
    else:
        ys = sorted({c[1] for c, score in points})
        y_indices = {y: i + 1 for i, y in enumerate(ys)}
        length = len(ys)
        if d == 2:
            tree = [None] * (length + 1)
            for record in left:
                limit = capacity[0] - record[1][0]
                while cursor < len(points) and points[cursor][0][0] <= limit:
                    c, score = points[cursor]
                    i = y_indices[c[1]]
                    while i <= length:
                        if tree[i] is None or score > tree[i]:
                            tree[i] = score
                        i += i & -i
                    cursor += 1
                i = bisect_right(ys, capacity[1] - record[1][1])
                winner = None
                while i:
                    score = tree[i]
                    if score is not None and (winner is None or score > winner):
                        winner = score
                    i -= i & -i
                consider(record, winner)
        else:
            # Only coordinates that can actually be updated are allocated.
            zs = [[] for _ in range(length + 1)]
            for c, score in points:
                i = y_indices[c[1]]
                while i <= length:
                    zs[i].append(c[2])
                    i += i & -i
            zs = [sorted(set(node)) for node in zs]
            trees = [[None] * (len(node) + 1) for node in zs]
            for record in left:
                limit = capacity[0] - record[1][0]
                while cursor < len(points) and points[cursor][0][0] <= limit:
                    c, score = points[cursor]
                    i = y_indices[c[1]]
                    while i <= length:
                        node = trees[i]
                        k = bisect_left(zs[i], c[2]) + 1
                        while k < len(node):
                            if node[k] is None or score > node[k]:
                                node[k] = score
                            k += k & -k
                        i += i & -i
                    cursor += 1
                i = bisect_right(ys, capacity[1] - record[1][1])
                z_limit = capacity[2] - record[1][2]
                winner = None
                while i:
                    node = trees[i]
                    k = bisect_right(zs[i], z_limit)
                    while k:
                        score = node[k]
                        if score is not None and (winner is None or score > winner):
                            winner = score
                        k -= k & -k
                    i -= i & -i
                consider(record, winner)
    return best[0], tuple(-best[j + 1] for j in range(d)), best[-1]


class _Search:
    def __init__(self, values, costs, closures, exclusions, budget):
        self.values = values
        self.costs = costs
        self.closures = closures
        self.budget = tuple(budget)
        self.d = len(budget)
        self.n = len(values)
        self.dependents = [0] * self.n
        for i, closure in enumerate(closures):
            bits = closure
            while bits:
                bit = bits & -bits
                bits -= bit
                self.dependents[bit.bit_length() - 1] |= 1 << i

        # Incompatibility includes conflicts anywhere in two dependency
        # closures, and thus also removes dependents of excluded projects.
        self.incompatible = [0] * self.n
        for i, closure in enumerate(closures):
            forbidden = 0
            bits = closure
            while bits:
                bit = bits & -bits
                bits -= bit
                forbidden |= exclusions[bit.bit_length() - 1]
            while forbidden:
                bit = forbidden & -forbidden
                forbidden -= bit
                self.incompatible[i] |= self.dependents[bit.bit_length() - 1]
        self.related = [((closures[i] | self.dependents[i] |
                          self.incompatible[i]) & ~(1 << i))
                        for i in range(self.n)]
        self.positive = sum(1 << i for i, v in enumerate(values) if v > 0)
        self.value_order = sorted(range(self.n), key=lambda i: values[i], reverse=True)

        # Integer cross products avoid precision loss for arbitrarily large
        # values and costs in fractional-knapsack upper bounds.
        self.ratio_orders = []
        for dimension in range(self.d):
            def compare(i, j):
                ci, cj = costs[i][dimension], costs[j][dimension]
                if not ci or not cj:
                    return (ci > cj) - (ci < cj)
                a, b = values[i] * cj, values[j] * ci
                return (b > a) - (b < a)
            self.ratio_orders.append(sorted(
                (i for i in range(self.n) if values[i] > 0),
                key=cmp_to_key(compare)))

        # Any clique in this graph contributes at most its largest positive
        # value. Two different partitions give inexpensive valid bounds.
        self.covers = []
        if any(self.incompatible):
            orders = [self.value_order, sorted(
                range(self.n),
                key=lambda i: self.incompatible[i].bit_count(), reverse=True)]
            for order in orders:
                remaining = self.positive
                cover = []
                for i in order:
                    if not remaining & (1 << i):
                        continue
                    clique = 1 << i
                    remaining &= ~clique
                    candidates = remaining & self.incompatible[i]
                    for j in order:
                        bit = 1 << j
                        if candidates & bit:
                            clique |= bit
                            remaining &= ~bit
                            candidates &= self.incompatible[j]
                    cover.append([j for j in self.value_order if clique & (1 << j)])
                self.covers.append(cover)
        self.best = None
        self.best_rank = None

    def totals(self, mask):
        value = 0
        costs = [0] * self.d
        while mask:
            bit = mask & -mask
            mask -= bit
            i = bit.bit_length() - 1
            value += self.values[i]
            for j in range(self.d):
                costs[j] += self.costs[i][j]
        return value, tuple(costs)

    def update(self, selected, value, costs):
        rank = (value, *(-c for c in costs), selected)
        if self.best_rank is None or rank > self.best_rank:
            self.best = value, costs, selected
            self.best_rank = rank

    def components(self, available):
        components = []
        remaining = available
        while remaining:
            frontier = remaining & -remaining
            component = 0
            while frontier:
                component |= frontier
                remaining &= ~frontier
                neighbors = 0
                while frontier:
                    bit = frontier & -frontier
                    frontier -= bit
                    neighbors |= self.related[bit.bit_length() - 1]
                frontier = neighbors & remaining
            components.append(component)
        return components

    def menu(self, component, capacity, limit=65536):
        """List local feasible choices, or abort if this component is large."""
        choices = {}
        leaves = 0

        def enumerate_choices(available, selected, value, costs):
            nonlocal leaves
            if not available:
                leaves += 1
                if leaves > limit:
                    raise OverflowError
                # The empty local selection dominates negative values, and
                # also zero values with a strictly positive cost.
                if value < 0 or (value == 0 and any(costs)):
                    return
                old = choices.get(costs)
                if old is None or (value, selected) > old:
                    choices[costs] = value, selected
                return
            i = available.bit_length() - 1
            add = self.closures[i] & available
            v, c = self.totals(add)
            totals = tuple(costs[j] + c[j] for j in range(self.d))
            if all(totals[j] <= capacity[j] for j in range(self.d)):
                enumerate_choices(available & ~(add | self.incompatible[i]),
                                  selected | add, value + v, totals)
            enumerate_choices(available & ~self.dependents[i], selected, value, costs)

        try:
            enumerate_choices(component, 0, 0, (0,) * self.d)
        except OverflowError:
            return None
        return [(v, c, m) for c, (v, m) in choices.items()]

    def finish_components(self, selected, available, value, costs):
        capacity = tuple(self.budget[j] - costs[j] for j in range(self.d))
        menus = []
        for component in self.components(available):
            menu = self.menu(component, capacity)
            if menu is None:
                return False
            menus.append(menu)
        result = _meet_in_middle(menus, capacity)
        if result is None:
            return False
        v, c, m = result
        self.update(selected | m, value + v,
                    tuple(costs[j] + c[j] for j in range(self.d)))
        return True

    def greedy(self, selected, available, value, costs, order):
        # This supplies an incumbent only; all correctness comes from the
        # exhaustive component joins or the proven search bounds.
        for i in order:
            if not available & (1 << i):
                continue
            add = self.closures[i] & available
            v, c = self.totals(add)
            totals = tuple(costs[j] + c[j] for j in range(self.d))
            if (v > 0 or (v == 0 and not any(c))) and all(
                    totals[j] <= self.budget[j] for j in range(self.d)):
                selected |= add
                available &= ~(add | self.incompatible[i])
                value += v
                costs = totals
        self.update(selected, value, costs)

    def promising(self, selected, available, value, costs):
        positives = available & self.positive
        upper = value
        bits = positives
        while bits:
            bit = bits & -bits
            bits -= bit
            upper += self.values[bit.bit_length() - 1]

        def possible(bound):
            if bound != self.best[0]:
                return bound > self.best[0]
            if costs != self.best[1]:
                return costs < self.best[1]
            return (selected | available) > self.best[2]

        if not possible(upper):
            return False
        for cover in self.covers:
            bound = value
            for clique in cover:
                for i in clique:
                    if positives & (1 << i):
                        bound += self.values[i]
                        break
            upper = min(upper, bound)
            if not possible(upper):
                return False
        for dimension, order in enumerate(self.ratio_orders):
            capacity = self.budget[dimension] - costs[dimension]
            bound = value
            for i in order:
                if not positives & (1 << i):
                    continue
                c = self.costs[i][dimension]
                if c <= capacity:
                    capacity -= c
                    bound += self.values[i]
                else:
                    bound += capacity * self.values[i] // c
                    break
            upper = min(upper, bound)
            if not possible(upper):
                return False
        return True

    def branch(self, selected, available, value, costs):
        self.update(selected, value, costs)
        if not available or not self.promising(selected, available, value, costs):
            return

        # Once constraints have disappeared, solve the remaining knapsack by
        # meet-in-the-middle rather than branching over its 2**k selections.
        constrained = [i for i in range(self.n)
                       if available & (1 << i) and self.related[i] & available]
        if not constrained:
            self.finish_components(selected, available, value, costs)
            return
        if available.bit_count() <= 12:
            self.finish_components(selected, available, value, costs)
            return
        components = self.components(available)
        if len(components) > 1:
            # Check a cheap worst-case bound before enumerating menus again.
            # Whole components must fit on one side of the join.
            sizes = [0, 0]
            for size in sorted((c.bit_count() for c in components), reverse=True):
                side = 0 if sizes[0] <= sizes[1] else 1
                sizes[side] += size
            if max(sizes) <= 17 and self.finish_components(
                    selected, available, value, costs):
                return
        i = max(constrained, key=lambda j: (
            ((self.dependents[j] | self.incompatible[j]) & available).bit_count(), j))
        add = self.closures[i] & available
        v, c = self.totals(add)
        totals = tuple(costs[j] + c[j] for j in range(self.d))

        def include():
            if all(totals[j] <= self.budget[j] for j in range(self.d)):
                self.branch(selected | add, available & ~(add | self.incompatible[i]),
                            value + v, totals)

        def exclude():
            self.branch(selected, available & ~self.dependents[i], value, costs)

        if self.best[2] & (1 << i):
            include()
            exclude()
        else:
            exclude()
            include()


def solve(projects, budget, required=()):
    """Return the globally optimal feasible portfolio, or None.

    Validation and dependency closure precede all feasibility reductions.
    Independent constraint components are enumerated and joined exactly;
    larger connected instances use branch-and-bound with safe relaxations.
    """
    names, values, costs, closures, exclusions, forced = _validate(
        projects, budget, required)
    search = _Search(values, costs, closures, exclusions, budget)
    value, totals = search.totals(forced)
    if any(totals[j] > budget[j] for j in range(len(budget))):
        return None
    forbidden = 0
    impossible = 0
    for i, closure in enumerate(closures):
        if forced & (1 << i):
            forbidden |= search.incompatible[i]
        _, c = search.totals(closure)
        if (search.incompatible[i] & (1 << i)
                or any(c[j] > budget[j] for j in range(len(budget)))):
            impossible |= 1 << i
    if forced & (forbidden | impossible):
        return None
    available = ((1 << len(names)) - 1) & ~(forced | forbidden | impossible)
    search.update(forced, value, totals)
    if not search.finish_components(forced, available, value, totals):
        for order in (search.value_order, list(range(len(names) - 1, -1, -1)),
                      *search.ratio_orders):
            search.greedy(forced, available, value, totals, order)
        search.branch(forced, available, value, totals)

    best_value, best_cost, best_mask = search.best
    # Among tied value/cost solutions, a maximum numeric mask beats every
    # other ID tuple except possibly its own proper prefixes. At the first
    # differing ID it includes the earlier ID; the other tuple wins only if
    # it ends there. Thus the shortest feasible equally scoring prefix of
    # this mask is precisely the contract's lexicographically smallest tuple.
    prefix = 0
    needed = 0
    prefix_value = 0
    prefix_cost = [0] * len(budget)
    selected = []
    for i in [None] + list(range(len(names) - 1, -1, -1)):
        if i is not None:
            if not best_mask & (1 << i):
                continue
            prefix |= 1 << i
            needed |= closures[i]
            prefix_value += values[i]
            for j in range(len(budget)):
                prefix_cost[j] += costs[i][j]
            selected.append(names[i])
        if (prefix & forced == forced and not needed & ~prefix
                and prefix_value == best_value and tuple(prefix_cost) == best_cost):
            return {"selected": selected, "value": best_value, "cost": list(best_cost)}
