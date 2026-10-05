"""Exact constrained portfolio optimization.

The public entry point is :func:`solve`.  The implementation deliberately uses
only small integer bit sets and Python's arbitrary precision integers; the
largest supported instance has only 32 projects.
"""

from functools import cmp_to_key
from math import gcd


_PROJECT_KEYS = {"id", "value", "cost", "requires", "excludes"}


def _integer(value):
    """Return whether *value* is an integer allowed by the contract."""

    return isinstance(value, int) and not isinstance(value, bool)


def _validate(projects, budget, required):
    """Validate and copy the useful input data into immutable structures."""

    if not isinstance(projects, list) or len(projects) > 32:
        raise ValueError("projects must be a list containing at most 32 items")
    if not isinstance(budget, list) or not 1 <= len(budget) <= 3:
        raise ValueError("budget must be a list with one to three dimensions")
    if any(not _integer(amount) or amount < 0 for amount in budget):
        raise ValueError("budget entries must be nonnegative integers")
    if not isinstance(required, (list, tuple)):
        raise ValueError("required must be a list or tuple")

    dimensions = len(budget)
    rows = []
    seen_ids = set()

    # First validate every project's local shape.  References are checked only
    # after all IDs have been collected, so forward references are supported.
    for project in projects:
        if not isinstance(project, dict) or set(project) != _PROJECT_KEYS:
            raise ValueError("each project must be a dictionary with exact keys")

        project_id = project["id"]
        value = project["value"]
        cost = project["cost"]
        requires = project["requires"]
        excludes = project["excludes"]

        if not isinstance(project_id, str) or not project_id:
            raise ValueError("project IDs must be nonempty strings")
        if project_id in seen_ids:
            raise ValueError("project IDs must be unique")
        seen_ids.add(project_id)

        if not _integer(value):
            raise ValueError("project values must be integers")
        if not isinstance(cost, list) or len(cost) != dimensions:
            raise ValueError("project costs must match the budget dimensions")
        if any(not _integer(amount) or amount < 0 for amount in cost):
            raise ValueError("project costs must be nonnegative integers")
        if not isinstance(requires, list) or not isinstance(excludes, list):
            raise ValueError("requires and excludes must be lists")

        for references in (requires, excludes):
            local_seen = set()
            for reference in references:
                if not isinstance(reference, str):
                    raise ValueError("project references must be strings")
                if reference in local_seen:
                    raise ValueError("project references must be distinct")
                local_seen.add(reference)

        rows.append(
            (project_id, value, tuple(cost), tuple(requires), tuple(excludes))
        )

    for project_id, _value, _cost, requires, excludes in rows:
        for reference in requires + excludes:
            if reference == project_id:
                raise ValueError("projects cannot refer to themselves")
            if reference not in seen_ids:
                raise ValueError("project references must name known IDs")

    required_ids = []
    required_seen = set()
    for project_id in required:
        if not isinstance(project_id, str) or project_id not in seen_ids:
            raise ValueError("required IDs must be known project IDs")
        if project_id in required_seen:
            raise ValueError("required IDs must be distinct")
        required_seen.add(project_id)
        required_ids.append(project_id)

    return rows, tuple(budget), tuple(required_ids)


def solve(projects, budget, required=()):
    """Return the globally optimal feasible portfolio, or ``None``.

    Besides exhaustive validation, the solver performs an exact branch-and-
    bound search.  Subsets are visited in Python tuple lexicographic order.
    This ordering is useful: after value and cost have been combined into an
    exact scalar score, the first subset attaining the maximum score is exactly
    the subset required by the final ID tie-break.
    """

    rows, budget, required_ids = _validate(projects, budget, required)
    dimensions = len(budget)
    count = len(rows)

    if count == 0:
        return {"selected": [], "value": 0, "cost": [0] * dimensions}

    # Sorted IDs make the recursive subset traversal agree exactly with Python
    # tuple ordering.  No caller-owned object is reordered or modified.
    rows.sort(key=lambda row: row[0])
    ids = tuple(row[0] for row in rows)
    index = {project_id: position for position, project_id in enumerate(ids)}
    values = tuple(row[1] for row in rows)
    costs = tuple(row[2] for row in rows)

    dependencies = [0] * count
    conflicts = [0] * count
    for position, row in enumerate(rows):
        for reference in row[3]:
            dependencies[position] |= 1 << index[reference]
        for reference in row[4]:
            other = index[reference]
            conflicts[position] |= 1 << other
            conflicts[other] |= 1 << position

    # DFS simultaneously checks acyclicity and builds every transitive
    # dependency closure (including the project itself).
    closures = [0] * count
    state = [0] * count

    def build_closure(position):
        if state[position] == 1:
            raise ValueError("the dependency graph must be acyclic")
        if state[position] == 2:
            return closures[position]
        state[position] = 1
        result = 1 << position
        remaining = dependencies[position]
        while remaining:
            bit = remaining & -remaining
            remaining ^= bit
            result |= build_closure(bit.bit_length() - 1)
        closures[position] = result
        state[position] = 2
        return result

    for position in range(count):
        build_closure(position)

    required_closure = 0
    for project_id in required_ids:
        required_closure |= closures[index[project_id]]

    # A feasible cost tuple is a mixed-radix number in [0, multiplier).  Thus
    # value * multiplier - encoded_cost orders solutions first by value and
    # then by the lexicographically smallest cost tuple, without approximation.
    cost_weights = [1] * dimensions
    multiplier = 1
    for dimension in range(dimensions - 1, -1, -1):
        cost_weights[dimension] = multiplier
        multiplier *= budget[dimension] + 1

    encoded_item_costs = tuple(
        sum(cost[dimension] * cost_weights[dimension]
            for dimension in range(dimensions))
        for cost in costs
    )
    scores = tuple(
        values[position] * multiplier - encoded_item_costs[position]
        for position in range(count)
    )
    positive_score_mask = 0
    for position, score in enumerate(scores):
        if score > 0:
            positive_score_mask |= 1 << position

    # A dependency closure that conflicts with itself can never be selected.
    closure_is_valid = [True] * count
    for position, closure in enumerate(closures):
        remaining = closure
        while remaining:
            bit = remaining & -remaining
            remaining ^= bit
            member = bit.bit_length() - 1
            if conflicts[member] & closure:
                closure_is_valid[position] = False
                break

    # A nonnegative weighted sum of budget constraints is itself a necessary
    # constraint.  Several such "surrogate" budgets make the fractional bound
    # much stronger on multidimensional instances (while every one remains a
    # relaxation).  Normalize vectors so scalar multiples are not duplicated.
    weight_vectors = []
    weight_vector_set = set()

    def add_weight_vector(vector):
        common = 0
        for coefficient in vector:
            common = gcd(common, coefficient)
        if common == 0:
            return
        normalized = tuple(coefficient // common for coefficient in vector)
        if normalized not in weight_vector_set:
            weight_vector_set.add(normalized)
            weight_vectors.append(normalized)

    for dimension in range(dimensions):
        add_weight_vector(tuple(
            1 if other == dimension else 0 for other in range(dimensions)
        ))
    if dimensions > 1:
        # All nonempty 0/1 combinations cover individual dimensions, pairs,
        # and the sum of all resources.
        for pattern in range(1, 1 << dimensions):
            add_weight_vector(tuple(
                1 if pattern & (1 << d) else 0 for d in range(dimensions)
            ))
        # These asymmetric directions cheaply sample additional Lagrange
        # multipliers and help when the resource scales differ.
        for singled_out in range(dimensions):
            add_weight_vector(tuple(
                2 if d == singled_out else 1 for d in range(dimensions)
            ))
        add_weight_vector(tuple(cost_weights))
        add_weight_vector(tuple(multiplier - weight for weight in cost_weights))
        add_weight_vector(tuple(
            multiplier // (budget[d] + 1) for d in range(dimensions)
        ))

    # Pre-sort fractional-knapsack candidates for each surrogate budget.  The
    # comparison is integer-only, avoiding floating-point rounding in bounds.
    relaxations = []
    for vector in weight_vectors:
        item_weights = tuple(
            sum(vector[d] * costs[position][d] for d in range(dimensions))
            for position in range(count)
        )

        def compare(left, right, item_weights=item_weights):
            left_cost = item_weights[left]
            right_cost = item_weights[right]
            if left_cost == 0 or right_cost == 0:
                if left_cost == right_cost:
                    return left - right
                return -1 if left_cost == 0 else 1
            cross_left = scores[left] * right_cost
            cross_right = scores[right] * left_cost
            if cross_left != cross_right:
                return -1 if cross_left > cross_right else 1
            return left - right

        order = [
            position for position in range(count) if scores[position] > 0
        ]
        order.sort(key=cmp_to_key(compare))
        relaxations.append((vector, item_weights, tuple(order)))

    # Static orders used to make weighted clique-cover bounds on exclusions.
    conflict_degrees = tuple(mask.bit_count() for mask in conflicts)
    clique_orders = (
        tuple(sorted(range(count),
                     key=lambda p: (-conflict_degrees[p], -scores[p], p))),
        tuple(sorted(range(count), key=lambda p: (-scores[p], p))),
    )

    def add_mask(mask, totals, score):
        """Add all members of mask to cost totals and scalar score."""

        totals = list(totals)
        while mask:
            bit = mask & -mask
            mask ^= bit
            position = bit.bit_length() - 1
            score += scores[position]
            for dimension in range(dimensions):
                totals[dimension] += costs[position][dimension]
        return tuple(totals), score

    root_cost, root_score = add_mask(
        required_closure, (0,) * dimensions, 0
    )
    if any(root_cost[d] > budget[d] for d in range(dimensions)):
        return None

    root_blocked = 0
    remaining = required_closure
    while remaining:
        bit = remaining & -remaining
        remaining ^= bit
        position = bit.bit_length() - 1
        if conflicts[position] & required_closure:
            return None
        root_blocked |= conflicts[position]

    best_score = None
    best_mask = 0
    best_value = 0
    best_cost = None
    # A greedy seed may occur later than the current lexicographic search
    # position.  Equal-bound pruning becomes safe only after the traversal has
    # itself encountered a portfolio with the incumbent score.
    best_seen_in_traversal = False

    def clique_cover_bound(candidate_mask, order):
        """Upper-bound independent-set score with a greedy clique cover."""

        clique_masks = []
        clique_maxima = []
        for position in order:
            bit = 1 << position
            if not candidate_mask & bit:
                continue
            for clique_number, clique in enumerate(clique_masks):
                if conflicts[position] & clique == clique:
                    clique_masks[clique_number] = clique | bit
                    if scores[position] > clique_maxima[clique_number]:
                        clique_maxima[clique_number] = scores[position]
                    break
            else:
                clique_masks.append(bit)
                clique_maxima.append(scores[position])
        return sum(clique_maxima)

    def remember(mask, score, totals):
        """Install a feasible incumbent, using the ID tie-break if necessary."""

        nonlocal best_score, best_mask, best_value, best_cost
        if best_score is not None and score < best_score:
            return
        if best_score is not None and score == best_score:
            old_ids = tuple(position for position in range(count)
                            if best_mask & (1 << position))
            new_ids = tuple(position for position in range(count)
                            if mask & (1 << position))
            if new_ids >= old_ids:
                return
        best_score = score
        best_mask = mask
        best_value = sum(values[position] for position in range(count)
                         if mask & (1 << position))
        best_cost = totals

    # Seed the proof search with several inexpensive feasible portfolios.  This
    # changes only the incumbent, never a bound.  In particular, projects are
    # added together with their full closures and only when the scalar score
    # strictly improves.
    greedy_orders = [
        tuple(sorted(range(count), key=lambda p: (-scores[p], p))),
        tuple(sorted(
            range(count),
            key=lambda p: (
                -sum(scores[m] for m in range(count)
                     if closures[p] & (1 << m)),
                p,
            ),
        )),
    ]
    greedy_orders.extend(order for _vector, _weights, order in relaxations)

    remember(required_closure, root_score, root_cost)
    for order in greedy_orders:
        mask = required_closure
        totals = root_cost
        score = root_score
        blocked = root_blocked
        for position in order:
            delta = closures[position] & ~mask
            if not delta:
                continue
            delta_score = 0
            scan = delta
            while scan:
                bit = scan & -scan
                scan ^= bit
                delta_score += scores[bit.bit_length() - 1]
            if delta_score <= 0:
                continue

            new_totals = list(totals)
            new_blocked = blocked
            seen = mask
            valid = True
            scan = delta
            while scan:
                bit = scan & -scan
                scan ^= bit
                member = bit.bit_length() - 1
                if conflicts[member] & seen:
                    valid = False
                    break
                seen |= bit
                new_blocked |= conflicts[member]
                for dimension in range(dimensions):
                    new_totals[dimension] += costs[member][dimension]
                    if new_totals[dimension] > budget[dimension]:
                        valid = False
                if not valid:
                    break
            if valid:
                mask |= delta
                totals = tuple(new_totals)
                score += delta_score
                blocked = new_blocked
        remember(mask, score, totals)

    def upper_bound(selected, last, closure, closure_cost, closure_score,
                    blocked):
        """Return an integer upper bound for every completion of this node."""

        prefix = (1 << (last + 1)) - 1
        skipped = prefix & ~selected
        candidates = positive_score_mask & ~prefix & ~closure

        # Remove projects that provably cannot occur in any completion.  This
        # filtering only tightens a relaxation; dependencies between the
        # surviving candidates are otherwise deliberately ignored.
        feasible_candidates = 0
        scan = candidates
        while scan:
            bit = scan & -scan
            scan ^= bit
            position = bit.bit_length() - 1
            project_closure = closures[position]
            if not closure_is_valid[position]:
                continue
            if project_closure & skipped:
                continue
            if project_closure & blocked:
                continue

            union_new = project_closure & ~closure
            possible = True
            for dimension in range(dimensions):
                added = 0
                new_members = union_new
                while new_members:
                    new_bit = new_members & -new_members
                    new_members ^= new_bit
                    added += costs[new_bit.bit_length() - 1][dimension]
                if closure_cost[dimension] + added > budget[dimension]:
                    possible = False
                    break
            if possible:
                feasible_candidates |= bit

        raw_extra = 0
        scan = feasible_candidates
        while scan:
            bit = scan & -scan
            scan ^= bit
            raw_extra += scores[bit.bit_length() - 1]
        best_extra_bound = raw_extra

        # Each surrogate budget gives a fractional-knapsack upper bound.
        # Flooring is safe because every actual scalar score is an integer.
        for vector, item_weights, order in relaxations:
            capacity = sum(
                vector[d] * (budget[d] - closure_cost[d])
                for d in range(dimensions)
            )
            extra = 0
            for position in order:
                bit = 1 << position
                if not feasible_candidates & bit:
                    continue
                weight = item_weights[position]
                if weight == 0:
                    extra += scores[position]
                elif weight <= capacity:
                    capacity -= weight
                    extra += scores[position]
                else:
                    extra += scores[position] * capacity // weight
                    break
            if extra < best_extra_bound:
                best_extra_bound = extra

        # Symmetric exclusions make selected projects an independent set.
        # Partitioning candidates into conflict cliques supplies another safe
        # (often much tighter) weighted upper bound.
        if feasible_candidates:
            edge_exists = False
            scan = feasible_candidates
            while scan:
                bit = scan & -scan
                scan ^= bit
                position = bit.bit_length() - 1
                if conflicts[position] & feasible_candidates:
                    edge_exists = True
                    break
            if edge_exists:
                for order in clique_orders:
                    cover = clique_cover_bound(feasible_candidates, order)
                    if cover < best_extra_bound:
                        best_extra_bound = cover

        return closure_score + best_extra_bound

    def search(selected, last, closure, closure_cost, closure_score, blocked):
        nonlocal best_seen_in_traversal

        # This subset is complete precisely when it already contains its whole
        # mandatory dependency/required closure.
        if selected == closure:
            if closure_score >= best_score:
                remember(selected, closure_score, closure_cost)
                best_seen_in_traversal = True

        node_bound = upper_bound(
            selected, last, closure, closure_cost, closure_score, blocked
        )
        # All subsets below this node occur later in lexicographic order.  An
        # equal score therefore cannot improve the ID tie-break.
        if (node_bound < best_score or
                (node_bound == best_score and best_seen_in_traversal)):
            return

        mandatory = closure & ~selected
        if mandatory:
            last_child = (mandatory & -mandatory).bit_length() - 1
        else:
            last_child = count - 1

        for position in range(last + 1, last_child + 1):
            bit = 1 << position
            new_selected = selected | bit
            new_closure = closure | closures[position]

            # Choosing this as the next ID permanently skips every smaller ID
            # not in selected.  Such an ID cannot also be mandatory.
            through_position = (1 << (position + 1)) - 1
            if new_closure & through_position != new_selected:
                continue

            delta = new_closure & ~closure
            new_cost = list(closure_cost)
            new_score = closure_score
            new_blocked = blocked
            seen = closure
            valid = True
            additions = delta
            while additions:
                new_bit = additions & -additions
                additions ^= new_bit
                member = new_bit.bit_length() - 1
                if conflicts[member] & seen:
                    valid = False
                    break
                seen |= new_bit
                new_blocked |= conflicts[member]
                new_score += scores[member]
                for dimension in range(dimensions):
                    new_cost[dimension] += costs[member][dimension]
                    if new_cost[dimension] > budget[dimension]:
                        valid = False
                if not valid:
                    break
            if not valid:
                continue

            search(
                new_selected,
                position,
                new_closure,
                tuple(new_cost),
                new_score,
                new_blocked,
            )

            # If a child attained this node's relaxation bound, no later child
            # can improve either the score or the lexicographic tie-break.
            if (best_score > node_bound or
                    (best_score == node_bound and best_seen_in_traversal)):
                break

    search(0, -1, required_closure, root_cost, root_score, root_blocked)

    if best_score is None:
        return None
    return {
        "selected": [ids[position] for position in range(count)
                     if best_mask & (1 << position)],
        "value": best_value,
        "cost": list(best_cost),
    }
