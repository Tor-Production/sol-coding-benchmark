"""Exact constrained portfolio optimization.

The public entry point is :func:`solve`.  The implementation deliberately
uses bit masks: the contract limits an instance to 32 projects, for which
dependency and conflict propagation becomes both simple and inexpensive.
"""

from bisect import bisect_left, bisect_right
from collections import deque
from functools import cmp_to_key


_KEYS = {"id", "value", "cost", "requires", "excludes"}


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _bits(mask):
    while mask:
        bit = mask & -mask
        yield bit.bit_length() - 1
        mask ^= bit


def _validate(projects, budget, required):
    """Validate and copy the input into an ID-sorted internal form."""
    if not isinstance(projects, list) or len(projects) > 32:
        raise ValueError("projects must be a list containing at most 32 items")
    if not isinstance(budget, list) or not 1 <= len(budget) <= 3:
        raise ValueError("budget must be a list with one to three dimensions")
    if any(not _is_int(x) or x < 0 for x in budget):
        raise ValueError("budget entries must be nonnegative integers")
    if not isinstance(required, (list, tuple)):
        raise ValueError("required must be a list or tuple")

    copied = []
    seen_ids = set()
    for project in projects:
        if not isinstance(project, dict) or set(project) != _KEYS:
            raise ValueError("each project must be a dictionary with exact keys")
        ident = project["id"]
        if not isinstance(ident, str) or not ident or ident in seen_ids:
            raise ValueError("project IDs must be unique nonempty strings")
        seen_ids.add(ident)

        value = project["value"]
        cost = project["cost"]
        requires = project["requires"]
        excludes = project["excludes"]
        if not _is_int(value):
            raise ValueError("project value must be an integer")
        if not isinstance(cost, list) or len(cost) != len(budget):
            raise ValueError("project cost has the wrong shape")
        if any(not _is_int(x) or x < 0 for x in cost):
            raise ValueError("project costs must be nonnegative integers")
        if not isinstance(requires, list) or not isinstance(excludes, list):
            raise ValueError("requires and excludes must be lists")

        # Do not use set(references) here: malformed unhashable references must
        # also become ValueError rather than leaking TypeError.
        for references in (requires, excludes):
            local = set()
            for reference in references:
                if not isinstance(reference, str) or not reference:
                    raise ValueError("references must be project IDs")
                if reference in local:
                    raise ValueError("duplicate project reference")
                local.add(reference)

        # Copy all mutable input fields; no later operation touches the input.
        copied.append((ident, value, tuple(cost), tuple(requires), tuple(excludes)))

    known = seen_ids
    for ident, _value, _cost, requires, excludes in copied:
        for reference in requires + excludes:
            if reference == ident or reference not in known:
                raise ValueError("self-references and unknown IDs are invalid")

    required_seen = set()
    required_copy = []
    for ident in required:
        if not isinstance(ident, str) or ident not in known or ident in required_seen:
            raise ValueError("required contains an invalid or duplicate ID")
        required_seen.add(ident)
        required_copy.append(ident)

    copied.sort(key=lambda row: row[0])
    ids = tuple(row[0] for row in copied)
    index = {ident: i for i, ident in enumerate(ids)}
    values = tuple(row[1] for row in copied)
    costs = tuple(row[2] for row in copied)
    requires = tuple(
        sum(1 << index[reference] for reference in row[3]) for row in copied
    )
    conflicts = [0] * len(copied)
    for i, row in enumerate(copied):
        for reference in row[4]:
            j = index[reference]
            conflicts[i] |= 1 << j
            conflicts[j] |= 1 << i

    # Kahn's algorithm validates the dependency DAG.  An edge goes from a
    # project to each prerequisite; either orientation detects the same cycle.
    indegree = [mask.bit_count() for mask in requires]
    reverse_direct = [0] * len(copied)
    for i, mask in enumerate(requires):
        for j in _bits(mask):
            reverse_direct[j] |= 1 << i
    queue = deque(i for i, degree in enumerate(indegree) if degree == 0)
    visited = 0
    while queue:
        prerequisite = queue.popleft()
        visited += 1
        dependents = reverse_direct[prerequisite]
        for dependent in _bits(dependents):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                queue.append(dependent)
    if visited != len(copied):
        raise ValueError("dependency graph contains a cycle")

    required_mask = sum(1 << index[ident] for ident in required_copy)
    return (
        ids,
        values,
        costs,
        requires,
        tuple(conflicts),
        tuple(budget),
        required_mask,
    )


class _Optimizer:
    def __init__(self, data):
        (
            self.ids,
            self.values,
            self.costs,
            self.requires,
            self.conflicts,
            self.budget,
            self.required_mask,
        ) = data
        self.n = len(self.ids)
        self.dimensions = len(self.budget)
        self.all_mask = (1 << self.n) - 1

        self.closure = [0] * self.n

        def make_closure(i):
            if self.closure[i]:
                return self.closure[i]
            result = 1 << i
            for prerequisite in _bits(self.requires[i]):
                result |= make_closure(prerequisite)
            self.closure[i] = result
            return result

        for i in range(self.n):
            make_closure(i)

        # dependents[i] contains every project whose transitive closure uses i,
        # including i.  Therefore excluding i excludes this entire mask.
        self.dependents = [0] * self.n
        for project, mask in enumerate(self.closure):
            for prerequisite in _bits(mask):
                self.dependents[prerequisite] |= 1 << project

        self._up_cache = {0: 0}

        # A lexicographic cost tuple can be represented by a mixed-radix
        # integer.  Feasible cost codes lie in [0, cost_space), so
        # value * cost_space - cost_code exactly captures objectives 1 and 2.
        weights = [1] * self.dimensions
        product = 1
        for d in range(self.dimensions - 1, -1, -1):
            weights[d] = product
            product *= self.budget[d] + 1
        self.cost_weights = tuple(weights)
        self.cost_space = product
        self.quality = tuple(
            self.values[i] * self.cost_space
            - sum(self.costs[i][d] * weights[d] for d in range(self.dimensions))
            for i in range(self.n)
        )

        # Selecting closure[i] forbids every project that needs a node in
        # conflict with that closure.
        self.select_block = [0] * self.n
        closure_conflicts = [0] * self.n
        for i, mask in enumerate(self.closure):
            raw = 0
            for member in _bits(mask):
                raw |= self.conflicts[member]
            closure_conflicts[i] = raw
            self.select_block[i] = self._upward(raw)

        # Some choices can be rejected once and for all.  All projects are
        # still validated before this point, as required by the contract.
        bad = 0
        for i, mask in enumerate(self.closure):
            totals = [0] * self.dimensions
            for member in _bits(mask):
                for d in range(self.dimensions):
                    totals[d] += self.costs[member][d]
            internally_conflicting = bool(closure_conflicts[i] & mask)
            if internally_conflicting or any(
                totals[d] > self.budget[d] for d in range(self.dimensions)
            ):
                bad |= 1 << i
        self.bad_mask = bad

        # Effective incompatibility is stronger than the declared conflict
        # graph: two projects are incompatible when their closures conflict.
        self.incompatible = [0] * self.n
        for i in range(self.n):
            for j in range(i + 1, self.n):
                if closure_conflicts[i] & self.closure[j]:
                    self.incompatible[i] |= 1 << j
                    self.incompatible[j] |= 1 << i

        self._make_fractional_orders()
        self._closure_bound_cache = {}
        self.best_quality = None
        self.best_mask = 0
        self.lex_complete = False
        self._last_target_complete = False

    def _upward(self, mask):
        cached = self._up_cache.get(mask)
        if cached is not None:
            return cached
        result = 0
        for i in _bits(mask):
            result |= self.dependents[i]
        if len(self._up_cache) < 8192:
            self._up_cache[mask] = result
        return result

    def _make_fractional_orders(self):
        vectors = []
        for d in range(self.dimensions):
            vector = [0] * self.dimensions
            vector[d] = 1
            vectors.append(tuple(vector))
        if self.dimensions > 1:
            vectors.append((1,) * self.dimensions)
            vectors.append(
                tuple(
                    self._other_budget_product(d)
                    for d in range(self.dimensions)
                )
            )
            vectors.append(self.cost_weights)

        unique = []
        for vector in vectors:
            if vector not in unique:
                unique.append(vector)

        self.fractional = []
        for vector in unique:
            scalar_cost = tuple(
                sum(self.costs[i][d] * vector[d] for d in range(self.dimensions))
                for i in range(self.n)
            )

            def compare(i, j, scalar_cost=scalar_cost):
                ci, cj = scalar_cost[i], scalar_cost[j]
                if ci == 0 or cj == 0:
                    if ci == cj:
                        return (self.quality[j] > self.quality[i]) - (
                            self.quality[j] < self.quality[i]
                        )
                    return -1 if ci == 0 else 1
                left = self.quality[i] * cj
                right = self.quality[j] * ci
                if left != right:
                    return -1 if left > right else 1
                return i - j

            positive = [i for i in range(self.n) if self.quality[i] > 0]
            positive.sort(key=cmp_to_key(compare))
            capacity = sum(self.budget[d] * vector[d] for d in range(self.dimensions))
            self.fractional.append((vector, scalar_cost, tuple(positive), capacity))

        # The first structurally unconstrained choice follows a balanced
        # density order, which also makes the fractional bound become tight
        # early on ordinary knapsack instances.
        self.branch_order = self.fractional[-1][2] + tuple(
            i for i in range(self.n) if self.quality[i] <= 0
        )

    def _other_budget_product(self, skipped):
        result = 1
        for d, limit in enumerate(self.budget):
            if d != skipped:
                result *= max(1, limit)
        return result

    def _mask_sums(self, mask):
        totals = [0] * self.dimensions
        quality = 0
        for i in _bits(mask):
            quality += self.quality[i]
            for d in range(self.dimensions):
                totals[d] += self.costs[i][d]
        return tuple(totals), quality

    def _initial_state(self, must_select, explicitly_excluded=0):
        selected = 0
        for i in _bits(must_select):
            selected |= self.closure[i]
        blocked = self.bad_mask | self._upward(explicitly_excluded)
        if selected & blocked:
            return None
        for i in _bits(selected):
            blocked |= self.select_block[i]
        if selected & blocked:
            return None
        totals, quality = self._mask_sums(selected)
        if any(totals[d] > self.budget[d] for d in range(self.dimensions)):
            return None
        return selected, blocked, totals, quality

    def _include(self, state, project):
        selected, blocked, totals, quality = state[:4]
        added = self.closure[project] & ~selected
        if added & blocked:
            return None
        new_totals = list(totals)
        new_quality = quality
        for i in _bits(added):
            new_quality += self.quality[i]
            for d in range(self.dimensions):
                new_totals[d] += self.costs[i][d]
        if any(new_totals[d] > self.budget[d] for d in range(self.dimensions)):
            return None
        new_selected = selected | added
        new_blocked = blocked | self.select_block[project]
        if new_selected & new_blocked:
            return None
        return new_selected, new_blocked, tuple(new_totals), new_quality

    def _normalize(self, state):
        """Apply safe, objective-preserving reductions to a search state."""
        selected, blocked, totals, quality = state[:4]
        while True:
            candidates = self.all_mask & ~selected & ~blocked

            # A closure which does not fit now can never fit later: all costs
            # are nonnegative and later selections only enlarge the union.
            newly_blocked = 0
            for i in _bits(candidates):
                trial = list(totals)
                for member in _bits(self.closure[i] & ~selected):
                    for d in range(self.dimensions):
                        trial[d] += self.costs[member][d]
                if any(trial[d] > self.budget[d] for d in range(self.dimensions)):
                    newly_blocked |= self.dependents[i]
            if newly_blocked & candidates:
                blocked |= newly_blocked
                continue

            candidates = self.all_mask & ~selected & ~blocked
            positive_roots = 0
            for i in _bits(candidates):
                if self.quality[i] > 0:
                    positive_roots |= 1 << i
            needed = 0
            for i in _bits(positive_roots):
                needed |= self.closure[i]
            dispensable = candidates & ~needed
            if dispensable:
                # Every nonpositive unforced node outside all positive
                # closures can be omitted in at least one optimum.
                blocked |= self._upward(dispensable)
                continue

            candidates = self.all_mask & ~selected & ~blocked
            forced = None
            for i in _bits(positive_roots & candidates):
                added = self.closure[i] & ~selected
                delta_quality = sum(self.quality[j] for j in _bits(added))
                if delta_quality <= 0:
                    continue
                zero_cost = all(
                    self.costs[j][d] == 0
                    for j in _bits(added)
                    for d in range(self.dimensions)
                )
                if not zero_cost:
                    continue
                harmed = self.select_block[i] & candidates & ~self.closure[i]
                if not harmed:
                    forced = i
                    break
            if forced is None:
                return selected, blocked, totals, quality, candidates
            included = self._include((selected, blocked, totals, quality), forced)
            if included is None:
                return None
            selected, blocked, totals, quality = included

    def _fractional_bound(self, candidates, totals, current_bound):
        best = current_bound
        for vector, scalar_cost, order, full_capacity in self.fractional:
            used = sum(totals[d] * vector[d] for d in range(self.dimensions))
            capacity = full_capacity - used
            result = 0
            for i in order:
                if not (candidates >> i) & 1:
                    continue
                cost = scalar_cost[i]
                if cost == 0:
                    result += self.quality[i]
                elif cost <= capacity:
                    result += self.quality[i]
                    capacity -= cost
                else:
                    result += self.quality[i] * capacity // cost
                    break
            if result < best:
                best = result
        return best

    def _conflict_bound(self, candidates, current_bound):
        positive = [i for i in _bits(candidates) if self.quality[i] > 0]
        if len(positive) < 2:
            return current_bound
        edge_mask = 0
        for i in positive:
            edge_mask |= self.incompatible[i] & candidates
        if not edge_mask:
            return current_bound

        orders = [
            sorted(positive, key=lambda i: (-self.quality[i], i)),
            sorted(
                positive,
                key=lambda i: (
                    -(self.incompatible[i] & candidates).bit_count(),
                    -self.quality[i],
                    i,
                ),
            ),
        ]
        best = current_bound
        for order in orders:
            cliques = []
            maxima = []
            for i in order:
                chosen = -1
                chosen_increase = self.quality[i] + 1
                for k, clique in enumerate(cliques):
                    if clique & ~self.incompatible[i] == 0:
                        increase = max(0, self.quality[i] - maxima[k])
                        if increase < chosen_increase:
                            chosen = k
                            chosen_increase = increase
                if chosen < 0:
                    cliques.append(1 << i)
                    maxima.append(self.quality[i])
                else:
                    cliques[chosen] |= 1 << i
                    if self.quality[i] > maxima[chosen]:
                        maxima[chosen] = self.quality[i]
            bound = sum(maxima)
            if bound < best:
                best = bound
        return best

    def _maximum_closure_bound(self, candidates):
        cached = self._closure_bound_cache.get(candidates)
        if cached is not None:
            return cached

        positives = sum(
            self.quality[i] for i in _bits(candidates) if self.quality[i] > 0
        )
        has_relevant_negative = any(
            self.quality[i] < 0
            and bool(self.dependents[i] & candidates & ~(1 << i))
            for i in _bits(candidates)
        )
        if not has_relevant_negative:
            return positives

        source, sink = self.n, self.n + 1
        graph = [[] for _ in range(self.n + 2)]

        def add_edge(start, end, capacity):
            graph[start].append([end, len(graph[end]), capacity])
            graph[end].append([start, len(graph[start]) - 1, 0])

        total_abs = 0
        for i in _bits(candidates):
            weight = self.quality[i]
            total_abs += abs(weight)
            if weight > 0:
                add_edge(source, i, weight)
            elif weight < 0:
                add_edge(i, sink, -weight)
        infinity = total_abs + 1
        for i in _bits(candidates):
            for prerequisite in _bits(self.requires[i] & candidates):
                add_edge(i, prerequisite, infinity)

        flow = 0
        while True:
            level = [-1] * len(graph)
            level[source] = 0
            queue = deque([source])
            while queue:
                node = queue.popleft()
                for end, _reverse, capacity in graph[node]:
                    if capacity and level[end] < 0:
                        level[end] = level[node] + 1
                        queue.append(end)
            if level[sink] < 0:
                break
            position = [0] * len(graph)

            def send(node, amount):
                if node == sink:
                    return amount
                while position[node] < len(graph[node]):
                    edge = graph[node][position[node]]
                    end, reverse, capacity = edge
                    if capacity and level[end] == level[node] + 1:
                        pushed = send(end, min(amount, capacity))
                        if pushed:
                            edge[2] -= pushed
                            graph[end][reverse][2] += pushed
                            return pushed
                    position[node] += 1
                return 0

            while True:
                pushed = send(source, infinity)
                if not pushed:
                    break
                flow += pushed

        result = positives - flow
        if len(self._closure_bound_cache) >= 8192:
            self._closure_bound_cache.clear()
        self._closure_bound_cache[candidates] = result
        return result

    def _upper_bound(self, normalized):
        _selected, _blocked, totals, _quality, candidates = normalized
        bound = sum(
            self.quality[i] for i in _bits(candidates) if self.quality[i] > 0
        )
        if not bound:
            return 0
        bound = self._fractional_bound(candidates, totals, bound)
        bound = self._conflict_bound(candidates, bound)
        closure_bound = self._maximum_closure_bound(candidates)
        return min(bound, closure_bound)

    def _lex_rank(self, mask):
        if not mask:
            return 0
        count = mask.bit_count()
        last = mask.bit_length() - 1
        deductions = sum(1 << (self.n - i - 1) for i in _bits(mask))
        return (1 << self.n) + count - deductions - (1 << (self.n - last - 1))

    def _record(self, selected, quality):
        if (
            self.best_quality is None
            or quality > self.best_quality
            or (
                quality == self.best_quality
                and self._lex_rank(selected) < self._lex_rank(self.best_mask)
            )
        ):
            self.best_quality = quality
            self.best_mask = selected

    def _choose(self, candidates):
        best = None
        best_structure = -1
        for i in _bits(candidates):
            structure = (
                3 * (self.incompatible[i] & candidates).bit_count()
                + 2 * ((self.dependents[i] & candidates) & ~(1 << i)).bit_count()
                + ((self.closure[i] & candidates) & ~(1 << i)).bit_count()
            )
            if structure > best_structure:
                best, best_structure = i, structure
            elif structure == best_structure and best is not None:
                if (self.quality[i], -i) > (self.quality[best], -best):
                    best = i
        if best_structure > 0:
            return best
        for i in self.branch_order:
            if (candidates >> i) & 1:
                return i
        return next(_bits(candidates))

    def _greedy(self, initial, mode):
        state = initial
        while True:
            normalized = self._normalize(state)
            if normalized is None:
                return
            selected, blocked, totals, quality, candidates = normalized
            self._record(selected, quality)
            chosen = None
            chosen_num = 0
            chosen_den = 1
            for i in _bits(candidates):
                trial = self._include((selected, blocked, totals, quality), i)
                if trial is None:
                    continue
                delta = trial[3] - quality
                if delta <= 0:
                    continue
                delta_cost = sum(
                    (trial[2][d] - totals[d]) * self._other_budget_product(d)
                    for d in range(self.dimensions)
                )
                denominator = delta_cost + 1
                if mode == 0:
                    numerator, denominator = delta, 1
                elif mode == 2:
                    numerator = delta * (self.n + 1)
                    denominator *= 1 + (self.select_block[i] & candidates).bit_count()
                else:
                    numerator = delta
                if (
                    chosen is None
                    or numerator * chosen_den > chosen_num * denominator
                    or (
                        numerator * chosen_den == chosen_num * denominator
                        and i < chosen
                    )
                ):
                    chosen = i
                    chosen_num, chosen_den = numerator, denominator
            if chosen is None:
                return
            state = self._include((selected, blocked, totals, quality), chosen)

    def _independent_items(self, normalized):
        """Return positive interaction-free items, or ``None``.

        Nonpositive optional projects can always be omitted while finding the
        first two objectives.  A positive project is interaction-free here
        when all of its still-unselected prerequisites are just itself and it
        conflicts with no other positive candidate.
        """
        selected, _blocked, _totals, _quality, candidates = normalized
        positive = 0
        for i in _bits(candidates):
            if self.quality[i] > 0:
                positive |= 1 << i
        for i in _bits(positive):
            if self.closure[i] & ~selected != 1 << i:
                return None
            if self.select_block[i] & positive:
                return None
        return tuple(_bits(positive))

    def _fully_independent(self, initial):
        """Identify all relevant optional nodes when they have no interaction.

        Negative-quality independent projects never occur in an optimum: they
        can simply be removed, improving quality and freeing budget.  Thus the
        relevant set consists of feasible positive and zero-quality nodes.
        """
        selected, blocked, totals, _quality = initial
        relevant = 0
        for i in _bits(self.all_mask & ~selected & ~blocked):
            if self.quality[i] < 0:
                continue
            added = self.closure[i] & ~selected
            trial = list(totals)
            for member in _bits(added):
                for d in range(self.dimensions):
                    trial[d] += self.costs[member][d]
            if any(trial[d] > self.budget[d] for d in range(self.dimensions)):
                continue
            if added != 1 << i:
                return None
            relevant |= 1 << i
        for i in _bits(relevant):
            if self.select_block[i] & relevant:
                return None
        return relevant

    def _enumerate_half(self, items, remaining):
        states = [((0,) * self.dimensions, 0, 0)]
        for project in items:
            additions = []
            project_cost = self.costs[project]
            bit = 1 << project
            for costs, quality, mask in states:
                new_costs = tuple(
                    costs[d] + project_cost[d] for d in range(self.dimensions)
                )
                if all(
                    new_costs[d] <= remaining[d]
                    for d in range(self.dimensions)
                ):
                    additions.append(
                        (new_costs, quality + self.quality[project], mask | bit)
                    )
            states.extend(additions)
        return states

    def _meet_in_middle(self, normalized, items, zero_mask=0):
        """Solve an interaction-free residual knapsack in O(2^(n/2))."""
        selected, _blocked, totals, base_quality, _candidates = normalized
        remaining = tuple(
            self.budget[d] - totals[d] for d in range(self.dimensions)
        )
        middle = len(items) // 2
        left_states = self._enumerate_half(items[:middle], remaining)
        right_states = self._enumerate_half(items[middle:], remaining)

        rank_cache = {}

        def augment(mask):
            mask |= selected
            if mask:
                # For a fixed nonempty selection, its lexicographically least
                # zero-quality extension contains every available zero ID
                # below its greatest ID and none after it.
                greatest = mask.bit_length() - 1
                mask |= zero_mask & ((1 << greatest) - 1)
            return mask

        def rank_with_base(mask):
            value = rank_cache.get(mask)
            if value is None:
                value = self._lex_rank(augment(mask))
                rank_cache[mask] = value
            return value

        def better(candidate, incumbent):
            """Compare (quality, optional-mask) summaries."""
            if candidate is None:
                return incumbent
            if incumbent is None or candidate[0] > incumbent[0]:
                return candidate
            if candidate[0] < incumbent[0]:
                return incumbent
            if rank_with_base(candidate[1]) < rank_with_base(incumbent[1]):
                return candidate
            return incumbent

        # Equal cost vectors have identical future compatibility.  Keeping
        # just their best quality (and best ID tuple on equality) reduces many
        # structured instances substantially before dominance queries.
        compressed = {}
        for costs, quality, mask in right_states:
            compressed[costs] = better((quality, mask), compressed.get(costs))
        right_states = [
            (costs, summary[0], summary[1])
            for costs, summary in compressed.items()
        ]

        best_quality = -1
        best_mask = selected

        def consider(left_quality, left_mask, right_summary):
            nonlocal best_quality, best_mask
            if right_summary is None:
                return
            quality = left_quality + right_summary[0]
            mask = augment(left_mask | right_summary[1])
            if quality > best_quality or (
                quality == best_quality
                and self._lex_rank(mask) < self._lex_rank(best_mask)
            ):
                best_quality, best_mask = quality, mask

        if self.dimensions == 1:
            right_states.sort(key=lambda state: state[0][0])
            queries = sorted(
                left_states,
                key=lambda state: remaining[0] - state[0][0],
            )
            position = 0
            summary = None
            for costs, quality, mask in queries:
                capacity = remaining[0] - costs[0]
                while (
                    position < len(right_states)
                    and right_states[position][0][0] <= capacity
                ):
                    state = right_states[position]
                    summary = better((state[1], state[2]), summary)
                    position += 1
                consider(quality, mask, summary)

        elif self.dimensions == 2:
            coordinates = sorted({state[0][1] for state in right_states})
            tree = [None] * (len(coordinates) + 1)

            def update(cost, summary):
                position = bisect_left(coordinates, cost) + 1
                while position < len(tree):
                    tree[position] = better(summary, tree[position])
                    position += position & -position

            def query(capacity):
                position = bisect_right(coordinates, capacity)
                summary = None
                while position:
                    summary = better(tree[position], summary)
                    position -= position & -position
                return summary

            right_states.sort(key=lambda state: state[0][0])
            queries = sorted(
                left_states,
                key=lambda state: remaining[0] - state[0][0],
            )
            position = 0
            for costs, quality, mask in queries:
                capacity0 = remaining[0] - costs[0]
                while (
                    position < len(right_states)
                    and right_states[position][0][0] <= capacity0
                ):
                    state = right_states[position]
                    update(state[0][1], (state[1], state[2]))
                    position += 1
                consider(quality, mask, query(remaining[1] - costs[1]))

        else:
            # Sweep the first cost.  A Fenwick tree of compressed Fenwick
            # trees answers the remaining two-dimensional dominance maximum.
            first_coordinates = sorted({state[0][1] for state in right_states})
            inner_coordinates = [[] for _ in range(len(first_coordinates) + 1)]
            for costs, _quality, _mask in right_states:
                position = bisect_left(first_coordinates, costs[1]) + 1
                while position < len(inner_coordinates):
                    inner_coordinates[position].append(costs[2])
                    position += position & -position
            for position in range(1, len(inner_coordinates)):
                inner_coordinates[position] = sorted(
                    set(inner_coordinates[position])
                )
            trees = [
                [None] * (len(coordinates) + 1)
                for coordinates in inner_coordinates
            ]

            def update(cost1, cost2, summary):
                outer = bisect_left(first_coordinates, cost1) + 1
                while outer < len(inner_coordinates):
                    coordinates = inner_coordinates[outer]
                    inner = bisect_left(coordinates, cost2) + 1
                    tree = trees[outer]
                    while inner < len(tree):
                        tree[inner] = better(summary, tree[inner])
                        inner += inner & -inner
                    outer += outer & -outer

            def query(capacity1, capacity2):
                outer = bisect_right(first_coordinates, capacity1)
                summary = None
                while outer:
                    coordinates = inner_coordinates[outer]
                    inner = bisect_right(coordinates, capacity2)
                    tree = trees[outer]
                    while inner:
                        summary = better(tree[inner], summary)
                        inner -= inner & -inner
                    outer -= outer & -outer
                return summary

            right_states.sort(key=lambda state: state[0][0])
            queries = sorted(
                left_states,
                key=lambda state: remaining[0] - state[0][0],
            )
            position = 0
            for costs, quality, mask in queries:
                capacity0 = remaining[0] - costs[0]
                while (
                    position < len(right_states)
                    and right_states[position][0][0] <= capacity0
                ):
                    state = right_states[position]
                    update(state[0][1], state[0][2], (state[1], state[2]))
                    position += 1
                consider(
                    quality,
                    mask,
                    query(remaining[1] - costs[1], remaining[2] - costs[2]),
                )

        return base_quality + best_quality, best_mask

    def _search(self, state):
        normalized = self._normalize(state)
        if normalized is None:
            return
        selected, blocked, totals, quality, candidates = normalized
        self._record(selected, quality)
        if not candidates:
            return
        if quality + self._upper_bound(normalized) <= self.best_quality:
            return

        project = self._choose(candidates)
        included = self._include((selected, blocked, totals, quality), project)
        excluded = (
            selected,
            blocked | self.dependents[project],
            totals,
            quality,
        )
        delta = included[3] - quality if included is not None else -1
        if included is not None and delta > 0:
            self._search(included)
            self._search(excluded)
        else:
            self._search(excluded)
            if included is not None:
                self._search(included)

    def optimize(self):
        initial = self._initial_state(self.required_mask)
        if initial is None:
            return None
        fully_independent = self._fully_independent(initial)
        normalized = self._normalize(initial)
        if normalized is None:
            return None
        independent = self._independent_items(normalized)
        if independent is not None:
            zero_mask = 0
            if fully_independent is not None:
                for i in _bits(fully_independent):
                    if self.quality[i] == 0:
                        zero_mask |= 1 << i
            quality, mask = self._meet_in_middle(
                normalized, independent, zero_mask
            )
            self.best_quality, self.best_mask = quality, mask
            self.lex_complete = fully_independent is not None
            return quality, mask
        self._record(initial[0], initial[3])
        for mode in range(3):
            self._greedy(initial, mode)
        self._search(initial)
        return self.best_quality, self.best_mask

    def _find_target(self, must_select, excluded, target):
        self._last_target_complete = False
        initial = self._initial_state(must_select, excluded)
        if initial is None:
            return None

        fully_independent = self._fully_independent(initial)
        normalized = self._normalize(initial)
        if normalized is None:
            return None
        independent = self._independent_items(normalized)
        if independent is not None:
            zero_mask = 0
            if fully_independent is not None:
                for i in _bits(fully_independent):
                    if self.quality[i] == 0:
                        zero_mask |= 1 << i
            quality, mask = self._meet_in_middle(
                normalized, independent, zero_mask
            )
            self._last_target_complete = fully_independent is not None
            return mask if quality == target else None

        # Cheap constructive attempts often find the target without opening
        # the exact feasibility search.
        old_quality, old_mask = self.best_quality, self.best_mask
        for mode in range(3):
            self.best_quality, self.best_mask = initial[3], initial[0]
            self._greedy(initial, mode)
            if self.best_quality == target:
                witness = self.best_mask
                self.best_quality, self.best_mask = old_quality, old_mask
                return witness
        self.best_quality, self.best_mask = old_quality, old_mask

        failed = set()

        def visit(state):
            normalized = self._normalize(state)
            if normalized is None:
                return None
            selected, blocked, totals, quality, candidates = normalized
            if quality == target:
                return selected
            if not candidates or quality + self._upper_bound(normalized) < target:
                return None
            key = (selected, blocked)
            if key in failed:
                return None
            project = self._choose(candidates)
            included = self._include((selected, blocked, totals, quality), project)
            excluded_state = (
                selected,
                blocked | self.dependents[project],
                totals,
                quality,
            )
            choices = (included, excluded_state)
            if included is not None and included[3] <= quality:
                choices = (excluded_state, included)
            for choice in choices:
                if choice is not None:
                    result = visit(choice)
                    if result is not None:
                        return result
            failed.add(key)
            return None

        return visit(initial)

    def lexicographically_smallest(self, target, witness):
        """Self-reduce target feasibility in sorted-tuple (preorder) order."""
        must = self.required_mask
        excluded = 0
        for i in range(self.n):
            state = self._initial_state(must, excluded)
            if state is None:  # Defensive; witness proves this cannot happen.
                return witness
            selected, _blocked, _totals, quality = state

            # Propagation can turn a structured residual into an independent
            # one.  Finish it directly rather than issuing many feasibility
            # queries for an already interaction-free suffix.
            fully_independent = self._fully_independent(state)
            if fully_independent is not None:
                normalized = self._normalize(state)
                independent = (
                    None
                    if normalized is None
                    else self._independent_items(normalized)
                )
                if independent is not None:
                    zero_mask = 0
                    for project in _bits(fully_independent):
                        if self.quality[project] == 0:
                            zero_mask |= 1 << project
                    result_quality, result_mask = self._meet_in_middle(
                        normalized, independent, zero_mask
                    )
                    if result_quality == target:
                        return result_mask

            # A tuple is smaller than every proper extension of itself.
            unprocessed = selected & ~((1 << i) - 1)
            if not unprocessed and quality == target:
                return selected

            bit = 1 << i
            if selected & bit:
                must |= bit
                continue

            if witness & bit:
                must |= bit
                continue

            alternative = self._find_target(must | bit, excluded, target)
            if alternative is not None:
                must |= bit
                witness = alternative
                if self._last_target_complete:
                    return alternative
            else:
                excluded |= bit

        final = self._initial_state(must, excluded)
        return final[0] if final is not None and final[3] == target else witness

    def result(self, mask):
        totals, _quality = self._mask_sums(mask)
        value = sum(self.values[i] for i in _bits(mask))
        return {
            "selected": [self.ids[i] for i in _bits(mask)],
            "value": value,
            "cost": list(totals),
        }


def solve(projects, budget, required=()):
    """Return the globally optimal feasible portfolio, or ``None``.

    Inputs are fully validated before feasibility or optimization is tested.
    """
    data = _validate(projects, budget, required)
    optimizer = _Optimizer(data)
    optimum = optimizer.optimize()
    if optimum is None:
        return None
    quality, witness = optimum
    selected = (
        witness
        if optimizer.lex_complete
        else optimizer.lexicographically_smallest(quality, witness)
    )
    return optimizer.result(selected)
