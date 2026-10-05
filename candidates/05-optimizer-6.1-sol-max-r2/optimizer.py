"""Exact portfolio optimization, using only the Python standard library."""

from bisect import bisect_left, bisect_right
from functools import cmp_to_key


def _validate(projects, budget, required):
    def integer(value):
        return isinstance(value, int) and not isinstance(value, bool)

    if not isinstance(projects, list) or len(projects) > 32:
        raise ValueError("projects must be a list of at most 32 dictionaries")
    if (not isinstance(budget, list) or not 1 <= len(budget) <= 3
            or any(not integer(x) or x < 0 for x in budget)):
        raise ValueError("budget must contain one to three nonnegative integers")
    if not isinstance(required, (list, tuple)):
        raise ValueError("required must be a list or tuple")

    keys = {"id", "value", "cost", "requires", "excludes"}
    by_id = {}
    for project in projects:
        if not isinstance(project, dict) or set(project) != keys:
            raise ValueError("each project must have exactly the specified keys")
        name = project["id"]
        if not isinstance(name, str) or not name or name in by_id:
            raise ValueError("project IDs must be distinct nonempty strings")
        if not integer(project["value"]):
            raise ValueError("project values must be integers")
        cost = project["cost"]
        if (not isinstance(cost, list) or len(cost) != len(budget)
                or any(not integer(x) or x < 0 for x in cost)):
            raise ValueError("project costs must match the budget dimensions")
        for field in ("requires", "excludes"):
            references = project[field]
            if not isinstance(references, list):
                raise ValueError("references must be lists")
            seen = set()
            for reference in references:
                if (not isinstance(reference, str) or not reference
                        or reference == name or reference in seen):
                    raise ValueError("invalid or duplicate project reference")
                seen.add(reference)
        by_id[name] = project

    names = sorted(by_id)
    indices = {name: i for i, name in enumerate(names)}
    values, costs, dependencies, exclusions = [], [], [], []
    for name in names:
        project = by_id[name]
        for field in ("requires", "excludes"):
            if any(reference not in indices for reference in project[field]):
                raise ValueError("unknown project reference")
        values.append(project["value"])
        costs.append(tuple(project["cost"]))
        dependencies.append(tuple(indices[x] for x in project["requires"]))
        exclusions.append(sum(1 << indices[x] for x in project["excludes"]))

    forced = 0
    for name in required:
        if not isinstance(name, str) or name not in indices:
            raise ValueError("unknown required project")
        bit = 1 << indices[name]
        if forced & bit:
            raise ValueError("duplicate required project")
        forced |= bit

    # Compute transitive closures while checking the entire dependency graph.
    closure = [0] * len(names)
    visiting = [False] * len(names)

    def visit(i):
        if visiting[i]:
            raise ValueError("cyclic dependencies")
        if closure[i]:
            return closure[i]
        visiting[i] = True
        mask = 1 << i
        for dependency in dependencies[i]:
            mask |= visit(dependency)
        visiting[i] = False
        closure[i] = mask
        return mask

    for i in range(len(names)):
        visit(i)
    # An exclusion declared in either direction forbids both directions.
    for i, mask in enumerate(exclusions):
        while mask:
            bit = mask & -mask
            exclusions[bit.bit_length() - 1] |= 1 << i
            mask ^= bit
    return names, values, costs, closure, exclusions, forced


def _join(left, right, capacity):
    """Maximize additive score across two tables with multidimensional costs.

    Tables map cost tuples to (score, mask). Offline dominance queries find
    the best right state inside each left state's remaining budget.
    """
    if len(left) < len(right):
        left, right = right, left
    if len(right) == 1:
        rc, (rs, rm) = next(iter(right.items()))
        best_score, best_mask = None, 0
        for lc, (ls, lm) in left.items():
            if all(x + y <= cap for x, y, cap in zip(lc, rc, capacity)):
                score = ls + rs
                if best_score is None or score > best_score:
                    best_score, best_mask = score, lm | rm
        return best_score, best_mask

    points = sorted((cost, score, mask) for cost, (score, mask) in right.items())
    queries = sorted((tuple(cap - c for cap, c in zip(capacity, cost)), score, mask)
                     for cost, (score, mask) in left.items())
    scores = [point[1] for point in points]
    best_score, best_mask = None, 0
    position = 0
    dimension = len(capacity)

    if dimension == 1:
        winner = -1
        for cap, score, mask in queries:
            while position < len(points) and points[position][0][0] <= cap[0]:
                if winner < 0 or scores[position] > scores[winner]:
                    winner = position
                position += 1
            if winner >= 0:
                total = score + scores[winner]
                if best_score is None or total > best_score:
                    best_score, best_mask = total, mask | points[winner][2]
        return best_score, best_mask

    ys = sorted({point[0][1] for point in points})
    size = len(ys)
    if dimension == 2:
        tree = [-1] * (size + 1)
        for cap, score, mask in queries:
            while position < len(points) and points[position][0][0] <= cap[0]:
                y = bisect_left(ys, points[position][0][1]) + 1
                while y <= size:
                    old = tree[y]
                    if old < 0 or scores[position] > scores[old]:
                        tree[y] = position
                    y += y & -y
                position += 1
            y = bisect_right(ys, cap[1])
            winner = -1
            while y:
                old = tree[y]
                if old >= 0 and (winner < 0 or scores[old] > scores[winner]):
                    winner = old
                y -= y & -y
            if winner >= 0:
                total = score + scores[winner]
                if best_score is None or total > best_score:
                    best_score, best_mask = total, mask | points[winner][2]
        return best_score, best_mask

    # A Fenwick tree over y, each node containing a compressed Fenwick tree
    # over z. Its O(m log m) storage avoids a dense two-dimensional grid.
    zs = [[] for _ in range(size + 1)]
    y_positions = []
    for cost, _, _ in points:
        y = bisect_left(ys, cost[1]) + 1
        y_positions.append(y)
        while y <= size:
            zs[y].append(cost[2])
            y += y & -y
    trees = [[]]
    for y in range(1, size + 1):
        zs[y] = sorted(set(zs[y]))
        trees.append([-1] * (len(zs[y]) + 1))

    for cap, score, mask in queries:
        while position < len(points) and points[position][0][0] <= cap[0]:
            y = y_positions[position]
            z_value = points[position][0][2]
            while y <= size:
                z = bisect_left(zs[y], z_value) + 1
                tree = trees[y]
                while z < len(tree):
                    old = tree[z]
                    if old < 0 or scores[position] > scores[old]:
                        tree[z] = position
                    z += z & -z
                y += y & -y
            position += 1
        y = bisect_right(ys, cap[1])
        winner = -1
        while y:
            z = bisect_right(zs[y], cap[2])
            tree = trees[y]
            while z:
                old = tree[z]
                if old >= 0 and (winner < 0 or scores[old] > scores[winner]):
                    winner = old
                z -= z & -z
            y -= y & -y
        if winner >= 0:
            total = score + scores[winner]
            if best_score is None or total > best_score:
                best_score, best_mask = total, mask | points[winner][2]
    return best_score, best_mask


class _Optimizer:
    # Enumeration is deliberately bounded: large connected components are
    # split by exact include/exclude branching instead of fully enumerated.
    ENUMERATION_LIMIT = 16000
    TABLE_LIMIT = 200000

    def __init__(self, values, costs, closure, exclusions, budget):
        self.n = len(values)
        self.costs = costs
        self.closure = closure
        self.exclusions = exclusions
        self.budget = tuple(budget)
        self.zero = (0,) * len(budget)

        # Mixed-radix costs encode their lexicographic order. Presence bits
        # give an additive tie-breaker; solve() fixes the prefix exception.
        radix = 1
        weights = [0] * len(budget)
        for j in range(len(budget) - 1, -1, -1):
            weights[j] = radix
            radix *= budget[j] + 1
        bit_base = 1 << self.n
        self.scores = [
            (values[i] * radix - sum(c * w for c, w in zip(costs[i], weights)))
            * bit_base + (1 << (self.n - 1 - i))
            for i in range(self.n)
        ]

        self.reverse = [0] * self.n
        for i, mask in enumerate(closure):
            while mask:
                bit = mask & -mask
                self.reverse[bit.bit_length() - 1] |= 1 << i
                mask ^= bit
        self.bans = [0] * self.n
        self.adjacency = [0] * self.n
        for i in range(self.n):
            self.adjacency[i] = closure[i] | self.reverse[i] | exclusions[i]
            mask = closure[i]
            excluded = 0
            while mask:
                bit = mask & -mask
                excluded |= exclusions[bit.bit_length() - 1]
                mask ^= bit
            while excluded:
                bit = excluded & -excluded
                self.bans[i] |= self.reverse[bit.bit_length() - 1]
                excluded ^= bit
        self.best_score = None
        self.best_mask = 0
        self.bounds = None

    def totals(self, mask):
        cost = list(self.zero)
        score = 0
        while mask:
            bit = mask & -mask
            i = bit.bit_length() - 1
            score += self.scores[i]
            for j, c in enumerate(self.costs[i]):
                cost[j] += c
            mask ^= bit
        return tuple(cost), score

    def components(self, mask):
        components = []
        while mask:
            frontier = mask & -mask
            component = 0
            while frontier:
                component |= frontier
                mask &= ~frontier
                neighbors = 0
                while frontier:
                    bit = frontier & -frontier
                    neighbors |= self.adjacency[bit.bit_length() - 1]
                    frontier ^= bit
                frontier = neighbors & mask
            components.append(component)
        return components

    def options(self, component, capacity):
        order = sorted((i for i in range(self.n) if component & (1 << i)),
                       key=lambda i: ((self.closure[i] | self.bans[i]
                                       | self.reverse[i]) & component).bit_count(),
                       reverse=True)
        table = {}
        visits = 0

        def enumerate_options(available, chosen, cost, score):
            nonlocal visits
            visits += 1
            if visits > self.ENUMERATION_LIMIT:
                return False
            if not available:
                old = table.get(cost)
                if old is None or score > old[0]:
                    table[cost] = (score, chosen)
                return True
            i = next(i for i in order if available & (1 << i))
            added = self.closure[i] & available
            added_cost, added_score = self.totals(added)
            new_cost = tuple(a + b for a, b in zip(cost, added_cost))
            if all(c <= cap for c, cap in zip(new_cost, capacity)):
                if not enumerate_options(available & ~(added | self.bans[i]),
                                         chosen | added, new_cost, score + added_score):
                    return False
            return enumerate_options(available & ~self.reverse[i], chosen, cost, score)

        if not enumerate_options(component, 0, self.zero, 0):
            return None
        return table

    def combine(self, tables, capacity):
        halves = [[], []]
        sizes = [1, 1]
        for _, table in sorted(tables, key=lambda entry: len(entry[1]), reverse=True):
            side = 0 if sizes[0] <= sizes[1] else 1
            halves[side].append(table)
            sizes[side] *= len(table)

        results = []
        for half in halves:
            states = {self.zero: (0, 0)}
            for table in half:
                expanded = {}
                for cost, (score, mask) in states.items():
                    for extra_cost, (extra_score, extra_mask) in table.items():
                        total_cost = tuple(a + b for a, b in zip(cost, extra_cost))
                        if any(c > cap for c, cap in zip(total_cost, capacity)):
                            continue
                        total_score = score + extra_score
                        old = expanded.get(total_cost)
                        if old is None or total_score > old[0]:
                            expanded[total_cost] = (total_score, mask | extra_mask)
                if len(expanded) > self.TABLE_LIMIT:
                    return None
                states = expanded
            results.append(states)
        return _join(results[0], results[1], capacity)

    def update(self, score, mask):
        if self.best_score is None or score > self.best_score:
            self.best_score, self.best_mask = score, mask

    def filter_available(self, available, capacity):
        # A closure that cannot fit cannot be selected, nor can any dependent.
        for i in range(self.n):
            if available & (1 << i):
                cost, _ = self.totals(self.closure[i] & available)
                if any(c > cap for c, cap in zip(cost, capacity)):
                    available &= ~self.reverse[i]
        # Nonpositive-score leaves can only worsen the objective. Repeat as
        # removing a dependent may expose another unnecessary negative leaf.
        previous = -1
        while previous != available:
            previous = available
            for i in range(self.n):
                bit = 1 << i
                if (available & bit and self.scores[i] < 0
                        and self.reverse[i] & available == bit):
                    available ^= bit
        return available

    def make_bounds(self):
        dimensions = len(self.budget)
        directions = [tuple(int(j == k) for j in range(dimensions))
                      for k in range(dimensions)]
        # Additional valid one-dimensional relaxations combine resource
        # dimensions, scaled by their budgets, to strengthen the bound.
        scale = 1
        for cap in self.budget:
            scale *= max(1, cap)
        normalized = tuple(scale // max(1, cap) for cap in self.budget)
        if dimensions > 1:
            directions.append(normalized)
            for k in range(dimensions):
                directions.append(tuple(w * (3 if j == k else 1)
                                        for j, w in enumerate(normalized)))
        positive = [i for i in range(self.n) if self.scores[i] > 0]
        bounds = []
        for direction in directions:
            costs = [sum(w * c for w, c in zip(direction, cost)) for cost in self.costs]

            def compare(a, b):
                if costs[a] == 0:
                    return -1 if costs[b] else 0
                if costs[b] == 0:
                    return 1
                difference = self.scores[b] * costs[a] - self.scores[a] * costs[b]
                return (difference > 0) - (difference < 0)

            order = sorted(positive, key=cmp_to_key(compare))
            bounds.append((direction, costs, order))
        self.bounds = bounds

    def bound(self, available, capacity, score):
        if self.bounds is None:
            self.make_bounds()
        upper = None
        for direction, costs, order in self.bounds:
            remaining = sum(w * c for w, c in zip(direction, capacity))
            extra = 0
            for i in order:
                if not available & (1 << i):
                    continue
                cost = costs[i]
                if cost <= remaining:
                    remaining -= cost
                    extra += self.scores[i]
                else:
                    extra += self.scores[i] * remaining // cost
                    break
            candidate = score + extra
            upper = candidate if upper is None else min(upper, candidate)
            if upper <= self.best_score:
                break
        return upper

    def greedy(self, chosen, available, cost, score):
        if self.bounds is None:
            self.make_bounds()
        orders = [sorted(range(self.n), key=lambda i: self.scores[i], reverse=True)]
        orders.extend(order for _, _, order in self.bounds)
        for order in orders:
            mask, remaining, total_cost, total_score = chosen, available, cost, score
            for i in order:
                if not remaining & (1 << i):
                    continue
                added = self.closure[i] & remaining
                extra_cost, extra_score = self.totals(added)
                new_cost = tuple(a + b for a, b in zip(total_cost, extra_cost))
                if extra_score > 0 and all(c <= cap for c, cap in zip(new_cost, self.budget)):
                    mask |= added
                    remaining &= ~(added | self.bans[i])
                    total_cost, total_score = new_cost, total_score + extra_score
            self.update(total_score, mask)

    def pivot(self, component, available):
        # Favor a decision that splits both branches into small components.
        # This picks the middle of paths and the hub of stars, for example.
        best = None
        winner = None
        mask = component
        while mask:
            bit = mask & -mask
            i = bit.bit_length() - 1
            branches = (component & ~self.reverse[i],
                        component & ~(self.closure[i] | self.bans[i]))
            parts = [part.bit_count() for branch in branches for part in self.components(branch)]
            _, added_score = self.totals(self.closure[i] & available)
            rank = (max(parts, default=0), sum(size * size for size in parts),
                    -added_score, i)
            if best is None or rank < best:
                best, winner = rank, i
            mask ^= bit
        return winner

    def search(self, chosen, available, cost, score):
        self.update(score, chosen)
        capacity = tuple(cap - c for cap, c in zip(self.budget, cost))
        available = self.filter_available(available, capacity)
        if not available:
            return
        if self.bound(available, capacity, score) <= self.best_score:
            return

        # If every remaining score is positive and everything fits together,
        # selecting everything attains the unconstrained upper bound.
        if all(self.scores[i] > 0 for i in range(self.n) if available & (1 << i)):
            full_cost, full_score = self.totals(available)
            conflict = any(self.exclusions[i] & available
                           for i in range(self.n) if available & (1 << i))
            if not conflict and all(c <= cap for c, cap in zip(full_cost, capacity)):
                self.update(score + full_score, chosen | available)
                return

        tables = []
        hard_component = None
        for component in sorted(self.components(available), key=int.bit_count, reverse=True):
            table = self.options(component, capacity)
            if table is None:
                hard_component = component
                break
            tables.append((component, table))
        if hard_component is None:
            result = self.combine(tables, capacity)
            if result is not None:
                extra_score, extra_mask = result
                self.update(score + extra_score, chosen | extra_mask)
                return
            hard_component = max((component for component, _ in tables), key=int.bit_count)

        i = self.pivot(hard_component, available)
        added = self.closure[i] & available
        extra_cost, extra_score = self.totals(added)
        new_cost = tuple(a + b for a, b in zip(cost, extra_cost))

        def include():
            if all(c <= cap for c, cap in zip(new_cost, self.budget)):
                self.search(chosen | added, available & ~(added | self.bans[i]),
                            new_cost, score + extra_score)

        def exclude():
            self.search(chosen, available & ~self.reverse[i], cost, score)

        if extra_score > 0:
            include()
            exclude()
        else:
            exclude()
            include()

    def run(self, forced):
        chosen = 0
        for i in range(self.n):
            if forced & (1 << i):
                chosen |= self.closure[i]
        cost, score = self.totals(chosen)
        if any(c > cap for c, cap in zip(cost, self.budget)):
            return None
        banned = 0
        impossible = 0
        for i in range(self.n):
            if chosen & (1 << i):
                banned |= self.bans[i]
            if self.closure[i] & self.bans[i]:
                impossible |= self.reverse[i]
        if chosen & (banned | impossible):
            return None
        available = ((1 << self.n) - 1) & ~(chosen | banned | impossible)
        capacity = tuple(cap - c for cap, c in zip(self.budget, cost))
        available = self.filter_available(available, capacity)
        self.update(score, chosen)
        self.greedy(chosen, available, cost, score)
        self.search(chosen, available, cost, score)
        return self.best_mask


def solve(projects, budget, required=()):
    """Return the globally optimal feasible portfolio, or None.

    Validation precedes every feasibility shortcut. Constraint propagation,
    component enumeration, meet-in-the-middle dominance queries, and exact
    branch-and-bound all preserve the complete feasible optimum.
    """
    names, values, costs, closure, exclusions, forced = _validate(projects, budget, required)
    optimizer = _Optimizer(values, costs, closure, exclusions, budget)
    selected = optimizer.run(forced)
    if selected is None:
        return None

    value = sum(values[i] for i in range(len(names)) if selected & (1 << i))
    cost, _ = optimizer.totals(selected)

    # Presence-bit maximization prefers including an earlier ID. Python tuple
    # order differs only when one tuple is a prefix of another. Any feasible
    # optimum lexicographically earlier than the presence-bit winner must be
    # its prefix (otherwise its first differing presence bit would be larger).
    # Thus checking the winner's prefixes gives the exact requested tie-break.
    prefix, prefix_value = 0, 0
    prefix_cost = [0] * len(budget)
    needed = 0
    for i in range(len(names) + 1):
        if (prefix & forced == forced and needed & ~prefix == 0
                and prefix_value == value and tuple(prefix_cost) == cost):
            selected = prefix
            break
        if i < len(names) and selected & (1 << i):
            prefix |= 1 << i
            needed |= closure[i]
            prefix_value += values[i]
            for j, c in enumerate(costs[i]):
                prefix_cost[j] += c
    return {"selected": [name for i, name in enumerate(names) if selected & (1 << i)],
            "value": value, "cost": list(cost)}
