"""Exact portfolio optimization using two half searches and bitset queries."""

from bisect import bisect_right
from functools import lru_cache
from fractions import Fraction


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _bits(mask):
    while mask:
        bit = mask & -mask
        yield bit.bit_length() - 1
        mask ^= bit


def _validate(projects, budget, required):
    if not isinstance(projects, list) or len(projects) > 32:
        raise ValueError("projects must be a list of at most 32 dictionaries")
    if (not isinstance(budget, list) or not 1 <= len(budget) <= 3
            or any(not _integer(x) or x < 0 for x in budget)):
        raise ValueError("budget must contain one to three nonnegative integers")
    if not isinstance(required, (list, tuple)):
        raise ValueError("required must be a list or tuple")

    keys = {"id", "value", "cost", "requires", "excludes"}
    by_id = {}
    for project in projects:
        if not isinstance(project, dict) or set(project) != keys:
            raise ValueError("a project must have exactly the specified keys")
        name = project["id"]
        if not isinstance(name, str) or not name or name in by_id:
            raise ValueError("project IDs must be unique nonempty strings")
        if not _integer(project["value"]):
            raise ValueError("project values must be integers, not bools")
        cost = project["cost"]
        if (not isinstance(cost, list) or len(cost) != len(budget)
                or any(not _integer(x) or x < 0 for x in cost)):
            raise ValueError("project costs must match the budget dimensions")
        for field in ("requires", "excludes"):
            refs = project[field]
            if not isinstance(refs, list):
                raise ValueError("project references must be lists")
            seen = set()
            for ref in refs:
                if (not isinstance(ref, str) or not ref or ref == name
                        or ref in seen):
                    raise ValueError("invalid or duplicate project reference")
                seen.add(ref)
        by_id[name] = project

    for project in projects:
        for field in ("requires", "excludes"):
            if any(ref not in by_id for ref in project[field]):
                raise ValueError("unknown project reference")
    seen = set()
    for name in required:
        if not isinstance(name, str) or name not in by_id or name in seen:
            raise ValueError("required IDs must be distinct known strings")
        seen.add(name)

    # Sorting here also makes a left-half tuple precede every right-half ID.
    ids = sorted(by_id)
    positions = {name: i for i, name in enumerate(ids)}
    values = [by_id[name]["value"] for name in ids]
    costs = [tuple(by_id[name]["cost"]) for name in ids]
    needs = [sum(1 << positions[ref] for ref in by_id[name]["requires"])
             for name in ids]
    conflicts = [0] * len(ids)
    for i, name in enumerate(ids):
        for ref in by_id[name]["excludes"]:
            j = positions[ref]
            conflicts[i] |= 1 << j
            conflicts[j] |= 1 << i

    # Validate the entire graph before doing any feasibility reductions.
    status = [0] * len(ids)
    closure = [0] * len(ids)

    def visit(i):
        if status[i] == 1:
            raise ValueError("cyclic project dependencies")
        if status[i] == 2:
            return closure[i]
        status[i] = 1
        result = 1 << i
        for j in _bits(needs[i]):
            result |= visit(j)
        closure[i] = result
        status[i] = 2
        return result

    for i in range(len(ids)):
        visit(i)
    mandatory = 0
    for name in required:
        mandatory |= closure[positions[name]]
    return ids, values, costs, closure, conflicts, mandatory


class _CostIndex:
    """Budget prefix sets, indexed by objective rank rather than cost rank.

    Store a byte prefix every 128 entries in cost order. An exact query copies
    one prefix and sets at most 127 additional bits. This avoids either a
    quadratic table of all prefixes or a linear scan of the other half.
    """

    _BLOCK = 128

    def __init__(self, records, dimension):
        size = len(records)
        order = sorted(range(size), key=lambda i: records[i][1][dimension])
        self.values = [records[i][1][dimension] for i in order]
        self.positions = [(i >> 3, 1 << (i & 7)) for i in order]
        prefix = bytearray((size + 7) // 8)
        self.prefixes = [bytes(prefix)]
        for k, (byte, bit) in enumerate(self.positions, 1):
            prefix[byte] |= bit
            if k % self._BLOCK == 0:
                self.prefixes.append(bytes(prefix))
        self.full = (1 << size) - 1
        # Many structured knapsacks have only a few distinct residual budgets.
        # Bound the cache: arbitrary large costs must not make storage quadratic.
        self._prefix = lru_cache(maxsize=128)(self._make_prefix)

    def _make_prefix(self, count):
        block, remainder = divmod(count, self._BLOCK)
        prefix = bytearray(self.prefixes[block])
        start = count - remainder
        for byte, bit in self.positions[start:count]:
            prefix[byte] |= bit
        return int.from_bytes(prefix, "little")

    def within(self, limit):
        count = bisect_right(self.values, limit)
        if count == len(self.values):
            return self.full
        if not count:
            return 0
        return self._prefix(count)


def solve(projects, budget, required=()):
    """Return the globally best feasible subset under all three tie breakers."""
    ids, values, costs, closure, conflicts, mandatory = _validate(
        projects, budget, required)
    n = len(ids)
    dimensions = len(budget)
    budget = tuple(budget)

    def totals(mask):
        value = 0
        cost = [0] * dimensions
        for i in _bits(mask):
            value += values[i]
            for d in range(dimensions):
                cost[d] += costs[i][d]
        return value, tuple(cost)

    def names(mask):
        return tuple(ids[i] for i in _bits(mask))

    mandatory_value, mandatory_cost = totals(mandatory)
    if any(mandatory_cost[d] > budget[d] for d in range(dimensions)):
        return None
    if any(conflicts[i] & mandatory for i in _bits(mandatory)):
        return None
    if not n:
        return {"selected": [], "value": 0, "cost": list(mandatory_cost)}

    dependents = [0] * n
    for i in range(n):
        for j in _bits(closure[i]):
            dependents[j] |= 1 << i

    # Two projects are incompatible if their dependency closures conflict.
    # This also propagates exclusion of a dependency to all its dependents.
    incompatible = [0] * n
    unavailable = 0
    for i in range(n):
        forbidden = 0
        for j in _bits(closure[i]):
            forbidden |= conflicts[j]
        for j in _bits(forbidden):
            incompatible[i] |= dependents[j]
        _, cost = totals(closure[i])
        if (forbidden & closure[i]
                or any(cost[d] > budget[d] for d in range(dimensions))):
            unavailable |= 1 << i
    for i in _bits(mandatory):
        unavailable |= incompatible[i]

    # Keep halves contiguous in ID order. Consequently, for a fixed left
    # selection the right tuple's own lexicographic order is the full tuple's
    # lexicographic order, including the subtle proper-prefix tie case.
    middle = n // 2
    left_all = (1 << middle) - 1
    right_all = (1 << (n - middle)) - 1
    left_mandatory = mandatory & left_all
    right_mandatory = mandatory >> middle
    left_blocked = unavailable & left_all
    right_blocked = unavailable >> middle
    left_needs = [closure[i] & left_all for i in range(middle)]
    right_needs = [closure[i] >> middle for i in range(middle, n)]
    left_conflicts = [incompatible[i] & left_all for i in range(middle)]
    right_conflicts = [incompatible[i] >> middle for i in range(middle, n)]
    left_dependents = [dependents[i] & left_all for i in range(middle)]
    right_dependents = [dependents[i] >> middle for i in range(middle, n)]
    left_fixed_value, left_fixed_cost = totals(left_mandatory)
    right_fixed_value, right_fixed_cost = totals(right_mandatory << middle)
    right_budget = tuple(budget[d] - left_fixed_cost[d]
                         for d in range(dimensions))
    left_budget = tuple(budget[d] - right_fixed_cost[d]
                        for d in range(dimensions))

    def add(mask, value, cost, offset):
        extra_value, extra_cost = totals(mask << offset)
        return (value + extra_value,
                tuple(cost[d] + extra_cost[d] for d in range(dimensions)))

    records = []

    def enumerate_right(chosen, blocked, value, cost):
        undecided = right_all & ~(chosen | blocked)
        if not undecided:
            # Lower ranks are better by value, costs, then right ID tuple.
            records.append((-value, cost, tuple(_bits(chosen)), chosen))
            return
        bit = undecided & -undecided
        i = bit.bit_length() - 1
        # Omitting a project also omits every project that needs it.
        enumerate_right(chosen, blocked | right_dependents[i], value, cost)
        added = right_needs[i] & ~chosen
        if added & blocked:
            return
        new_blocked = blocked | right_conflicts[i]
        if new_blocked & (chosen | added):
            return
        new_value, new_cost = add(added, value, cost, middle)
        if any(new_cost[d] > right_budget[d] for d in range(dimensions)):
            return
        enumerate_right(chosen | added, new_blocked, new_value, new_cost)

    enumerate_right(right_mandatory, right_blocked,
                    right_fixed_value, right_fixed_cost)
    records.sort()
    size = len(records)
    full = (1 << size) - 1

    # Membership bitsets encode every cross-half implication and exclusion.
    membership = [bytearray((size + 7) // 8) for _ in range(n - middle)]
    for rank, record in enumerate(records):
        byte, bit = rank >> 3, 1 << (rank & 7)
        for i in _bits(record[3]):
            membership[i][byte] |= bit
    membership = [int.from_bytes(x, "little") for x in membership]
    allow_in = []
    allow_out = []
    for i in range(middle):
        included = full
        for j in _bits(closure[i] >> middle):
            included &= membership[j]
        for j in _bits(incompatible[i] >> middle):
            included &= full ^ membership[j]
        omitted = full
        for j in _bits(dependents[i] >> middle):
            omitted &= full ^ membership[j]
        allow_in.append(None if included == full else included)
        allow_out.append(None if omitted == full else omitted)

    indexes = [None] * dimensions

    def best_right(allowed, residual):
        # Construct a cost index lazily, and only query dimensions that actually
        # reject the current best candidate. Each dimension is applied at most
        # once; bitset intersection then jumps directly to the next candidate.
        while allowed:
            rank = 0 if allowed & 1 else (allowed & -allowed).bit_length() - 1
            record = records[rank]
            for d in range(dimensions):
                if record[1][d] > residual[d]:
                    if indexes[d] is None:
                        indexes[d] = _CostIndex(records, d)
                    allowed &= indexes[d].within(residual[d])
                    break
            else:
                return record
        return None

    best_value = mandatory_value
    best_cost = mandatory_cost
    best_names = names(mandatory)
    positive = [max(value, 0) for value in values[:middle]]
    optional = ((1 << n) - 1) & ~(mandatory | unavailable)
    right_optional = optional & ~left_all

    # Relax dependencies/exclusions and all but one budget dimension. The
    # fractional knapsack optimum is an upper bound on every integral feasible
    # extension. Ratios and the fractional cutoff use exact integer arithmetic.
    relaxations = []
    seen_dimensions = {}
    prices = []
    for d in range(dimensions):
        signature = (budget[d], tuple(cost[d] for cost in costs))
        if signature in seen_dimensions:
            prices.append(seen_dimensions[signature])
            continue
        free = []
        charged = []
        for i in _bits(optional):
            if values[i] <= 0:
                continue
            item = (1 << i, values[i], costs[i][d])
            if costs[i][d] == 0:
                free.append(item)
            else:
                charged.append(item)
        charged.sort(key=lambda item: Fraction(item[1], item[2]), reverse=True)
        relaxations.append((d, free, charged))
        price = (free, charged[0][1:] if charged else None)
        prices.append(price)
        seen_dimensions[signature] = price

    def fractional_bound(value, cost, available, upper):
        for d, free, charged in relaxations:
            capacity = budget[d] - cost[d] - right_fixed_cost[d]
            bound = value + right_fixed_value
            for bit, gain, _ in free:
                if available & bit:
                    bound += gain
            for bit, gain, expense in charged:
                if not available & bit:
                    continue
                if expense <= capacity:
                    capacity -= expense
                    bound += gain
                else:
                    bound += gain * capacity // expense
                    break
            upper = min(upper, bound)
            if upper < best_value:
                break
        return upper

    def minimum_cost(value, cost, available):
        lower = []
        for d, (free, price) in enumerate(prices):
            free_value = sum(gain for bit, gain, _ in free if available & bit)
            needed = best_value - value - right_fixed_value - free_value
            extra = 0
            if needed > 0 and price is not None:
                gain, expense = price
                # Even unlimited copies of the most efficient project cannot
                # supply this value with less cost. Round upward exactly.
                extra = (needed * expense + gain - 1) // gain
            lower.append(cost[d] + right_fixed_cost[d] + extra)
        return tuple(lower)

    def filter_right(allowed, included, omitted):
        for i in _bits(included):
            if allow_in[i] is not None:
                allowed &= allow_in[i]
        for i in _bits(omitted):
            if allow_out[i] is not None:
                allowed &= allow_out[i]
        return allowed

    def search_left(chosen, blocked, value, cost, allowed, remaining_positive):
        nonlocal best_value, best_cost, best_names
        if not allowed:
            return
        rank = 0 if allowed & 1 else (allowed & -allowed).bit_length() - 1
        upper_value = value + remaining_positive - records[rank][0]
        if upper_value < best_value:
            return
        undecided = left_all & ~(chosen | blocked)
        available = undecided | right_optional
        upper_value = fractional_bound(value, cost, available, upper_value)
        if upper_value < best_value:
            return
        if upper_value == best_value:
            lower_cost = minimum_cost(value, cost, available)
            if lower_cost > best_cost:
                return
            if lower_cost == best_cost:
                fixed = chosen | mandatory
                # Among arbitrary extensions of fixed, the smallest ID tuple
                # adds all available IDs before its last ID and none after it.
                before_last = (1 << (fixed.bit_length() - 1)) - 1 if fixed else 0
                lexical_lower = names(fixed | (available & before_last))
                if lexical_lower >= best_names:
                    return
        if not undecided:
            residual = tuple(budget[d] - cost[d] for d in range(dimensions))
            record = best_right(allowed, residual)
            if record is None:
                return
            total_value = value - record[0]
            total_cost = tuple(cost[d] + record[1][d] for d in range(dimensions))
            if (total_value < best_value
                    or (total_value == best_value and total_cost > best_cost)):
                return
            mask = chosen | (record[3] << middle)
            selected = names(mask)
            if (total_value > best_value or total_cost < best_cost
                    or selected < best_names):
                best_value, best_cost = total_value, total_cost
                best_names = selected
            return

        bit = undecided & -undecided
        i = bit.bit_length() - 1

        def include():
            added = left_needs[i] & ~chosen
            if added & blocked:
                return
            new_blocked = blocked | left_conflicts[i]
            if new_blocked & (chosen | added):
                return
            new_value, new_cost = add(added, value, cost, 0)
            if any(new_cost[d] > left_budget[d] for d in range(dimensions)):
                return
            omitted = new_blocked & ~blocked
            new_allowed = filter_right(allowed, added, omitted)
            reduction = sum(positive[j] for j in _bits(added | omitted))
            search_left(chosen | added, new_blocked, new_value, new_cost,
                        new_allowed, remaining_positive - reduction)

        def omit():
            omitted = left_dependents[i] & ~blocked
            new_allowed = filter_right(allowed, 0, omitted)
            reduction = sum(positive[j] for j in _bits(omitted))
            search_left(chosen, blocked | omitted, value, cost,
                        new_allowed, remaining_positive - reduction)

        # A good incumbent strengthens the safe value bound; neither branch
        # is discarded merely because this ordering prefers the other one.
        if values[i] >= 0:
            include()
            omit()
        else:
            omit()
            include()

    initial_allowed = filter_right(full, left_mandatory, left_blocked)
    remaining_positive = sum(positive[i] for i in _bits(
        left_all & ~(left_mandatory | left_blocked)))
    search_left(left_mandatory, left_blocked, left_fixed_value, left_fixed_cost,
                initial_allowed, remaining_positive)
    return {"selected": list(best_names), "value": best_value,
            "cost": list(best_cost)}
