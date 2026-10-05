"""Reference: topological branch-and-bound; independent oracle is in evaluator."""
import heapq

def integer(x, nonnegative=False):
    return isinstance(x, int) and not isinstance(x, bool) and (not nonnegative or x >= 0)

def solve(projects, budget, required=()):
    if not isinstance(budget, list) or not 1 <= len(budget) <= 3 or not all(integer(x, True) for x in budget):
        raise ValueError('budget')
    if not isinstance(projects, list) or len(projects) > 32:
        raise ValueError('projects')
    by_id = {}
    for p in projects:
        if not isinstance(p, dict) or set(p) != {'id', 'value', 'cost', 'requires', 'excludes'}:
            raise ValueError('project')
        name = p['id']
        if not isinstance(name, str) or not name or name in by_id or not integer(p['value']):
            raise ValueError('id/value')
        if not isinstance(p['cost'], list) or len(p['cost']) != len(budget) or not all(integer(c, True) for c in p['cost']):
            raise ValueError('cost')
        by_id[name] = p
    for p in projects:
        for field in ('requires', 'excludes'):
            refs = p[field]
            if not isinstance(refs, list) or any(not isinstance(x, str) or not x or x == p['id'] or x not in by_id for x in refs):
                raise ValueError(field)
            if len(set(refs)) != len(refs):
                raise ValueError('duplicate reference')
    if not isinstance(required, (list, tuple)) or any(not isinstance(x, str) or x not in by_id for x in required) or len(set(required)) != len(required):
        raise ValueError('required')
    children = {x: [] for x in by_id}
    degrees = {x: len(p['requires']) for x, p in by_id.items()}
    for x, p in by_id.items():
        for parent in p['requires']:
            children[parent].append(x)
    ready = [x for x in by_id if not degrees[x]]
    heapq.heapify(ready)
    order = []
    while ready:
        x = heapq.heappop(ready); order.append(x)
        for child in children[x]:
            degrees[child] -= 1
            if not degrees[child]:
                heapq.heappush(ready, child)
    if len(order) != len(projects):
        raise ValueError('cycle')
    must = set(required)
    for x in reversed(order):
        if x in must:
            must.update(by_id[x]['requires'])
    indexes = {x: i for i, x in enumerate(order)}
    conflicts = [0] * len(order)
    for x, p in by_id.items():
        for y in p['excludes']:
            conflicts[indexes[x]] |= 1 << indexes[y]
            conflicts[indexes[y]] |= 1 << indexes[x]
    upper = [0] * (len(order) + 1)
    for i in reversed(range(len(order))):
        p = by_id[order[i]]
        individually_feasible = all(c <= b for c, b in zip(p['cost'], budget))
        upper[i] = upper[i+1] + (max(0, p['value']) if individually_feasible else 0)
    best = None
    best_key = None
    def search(i, chosen, mask, spent, value):
        nonlocal best, best_key
        if best is not None and value + upper[i] < best['value']:
            return
        if i == len(order):
            ids = sorted(chosen)
            key = (-value, tuple(spent), tuple(ids))
            if best_key is None or key < best_key:
                best_key = key
                best = dict(selected=ids, value=value, cost=list(spent))
            return
        name = order[i]; p = by_id[name]
        can = not (mask & conflicts[i])
        if any(x not in chosen for x in p["requires"]):
            can = False
        costs = tuple(a+b for a, b in zip(spent, p['cost']))
        if any(a > b for a, b in zip(costs, budget)):
            can = False
        if can:
            search(i+1, chosen | {name}, mask | (1 << i), costs, value + p['value'])
        if name not in must:
            search(i+1, chosen, mask, spent, value)
    search(0, set(), 0, (0,) * len(budget), 0)
    return best
