import copy
import itertools
import random
import unittest

from optimizer import _independent, solve


def project(name, value, cost, requires=(), excludes=()):
    return dict(id=name, value=value, cost=list(cost),
                requires=list(requires), excludes=list(excludes))


def exhaustive(projects, budget, required=()):
    """A small, intentionally straightforward oracle from the contract."""
    best, answer = None, None
    for bits in itertools.product((False, True), repeat=len(projects)):
        chosen = [p for p, bit in zip(projects, bits) if bit]
        names = {p['id'] for p in chosen}
        if not set(required) <= names:
            continue
        if any(not set(p['requires']) <= names or set(p['excludes']) & names for p in chosen):
            continue
        cost = tuple(sum(p['cost'][d] for p in chosen) for d in range(len(budget)))
        if any(c > b for c, b in zip(cost, budget)):
            continue
        value = sum(p['value'] for p in chosen)
        ids = tuple(sorted(names))
        key = (-value, cost, ids)
        if best is None or key < best:
            best = key
            answer = dict(selected=list(ids), value=value, cost=list(cost))
    return answer


class Contract(unittest.TestCase):
    def test_random_graphs_against_exhaustive(self):
        rng = random.Random(721)
        for case in range(350):
            n, dims = rng.randrange(11), rng.randrange(1, 4)
            names = ['p%02d' % i for i in range(n)]
            projects = []
            for i, name in enumerate(names):
                needs = [names[j] for j in range(i) if rng.random() < .17]
                excludes = [names[j] for j in range(n) if j != i and rng.random() < .09]
                projects.append(project(name, rng.randrange(-6, 10),
                                        [rng.randrange(5) for _ in range(dims)], needs, excludes))
            rng.shuffle(projects)
            budget = [rng.randrange(12) for _ in range(dims)]
            required = [name for name in names if rng.random() < .12]
            with self.subTest(case=case):
                self.assertEqual(solve(projects, budget, required), exhaustive(projects, budget, required))

    def test_dominance_queries_against_exhaustive(self):
        rng = random.Random(732)
        for dims in (1, 2, 3):
            for case in range(35):
                n = 10
                values = [rng.randrange(1, 9) for _ in range(n)]
                costs = [tuple(rng.randrange(5) for _ in range(dims)) for _ in range(n)]
                budget = tuple(rng.randrange(13) for _ in range(dims))
                mask, value, cost = _independent(list(range(n)), values, costs, budget, dims, n)
                actual = dict(selected=['p%02d' % i for i in range(n) if mask & (1 << i)],
                              value=value, cost=list(cost))
                projects = [project('p%02d' % i, values[i], costs[i]) for i in range(n)]
                with self.subTest(dims=dims, case=case):
                    self.assertEqual(actual, exhaustive(projects, budget))

    def test_prefix_ties_and_free_projects(self):
        self.assertEqual(solve([project('z', 1, [0]), project('a', 0, [0]),
                                project('zz', 0, [0])], [0])['selected'], ['a', 'z'])
        projects = [project('a', 0, [0]), project('b', 0, [0], excludes=['a']),
                    project('z', 0, [0])]
        self.assertEqual(solve(projects, [0], ['z'])['selected'], ['a', 'z'])
        self.assertEqual(solve([project('z', 0, [0]), project('a', 0, [0])], [0])['selected'], [])
        projects = [project('b', 5, [0]), project('a', -5, [0]),
                    project('z', 7, [0], requires=['a', 'b'])]
        self.assertEqual(solve(projects, [0]), exhaustive(projects, [0]))

    def test_cost_dimension_order(self):
        projects = [project('a', 4, [2, 0, 0]), project('z', 4, [1, 9, 9])]
        self.assertEqual(solve(projects, [2, 9, 9])['selected'], ['z'])

    def test_huge_integers(self):
        big = 10 ** 400
        projects = [project('a', big + 1, [big]), project('b', big, [big - 1]),
                    project('c', 1, [1])]
        self.assertEqual(solve(projects, [big]), exhaustive(projects, [big]))

    def test_no_mutation(self):
        projects = [project('z', 8, [3, 1], requires=['a']), project('a', -1, [0, 0])]
        budget, required = [3, 2], ['z']
        before = copy.deepcopy((projects, budget, required))
        solve(projects, budget, required)
        self.assertEqual((projects, budget, required), before)

    def test_validation(self):
        valid = project('a', 1, [0])
        cases = [
            ((), [1], ()), ([valid] * 33, [1], ()), ([valid], (), ()),
            ([valid], [], ()), ([valid], [1] * 4, ()), ([valid], [True], ()),
            ([valid], [-1], ()), ([valid], [1.0], ()), ([valid], [1], 'a'),
            ([valid], [1], ['missing']), ([valid], [1], ['a', 'a']),
            ([valid], [1], [False]), ([valid, valid], [1], ()),
        ]
        for field, value in [('id', ''), ('id', []), ('value', True), ('value', 1.0),
                             ('cost', (0,)), ('cost', [False]), ('cost', [-1]), ('cost', [0, 0]),
                             ('requires', ()), ('requires', ['a']), ('requires', ['missing']),
                             ('requires', [None]), ('excludes', ['a']), ('excludes', ['missing'])]:
            p = dict(valid)
            p[field] = value
            cases.append(([p], [1], ()))
        extra = dict(valid, extra=1)
        missing = dict(valid)
        del missing['excludes']
        cases.extend([([extra], [1], ()), ([missing], [1], ()), ([None], [1], ())])
        cases.append(([project('a', 1, [100], requires=['b']),
                       project('b', 1, [100], requires=['a'])], [0], ()))
        for field in ('requires', 'excludes'):
            p = dict(valid)
            p[field] = ['b', 'b']
            cases.append(([p, project('b', 0, [0])], [1], ()))
        for i, args in enumerate(cases):
            with self.subTest(case=i), self.assertRaises(ValueError):
                solve(*args)

    def test_32_independent_and_zero_ties(self):
        projects = [project('p%02d' % i, 1, [1, 1, 1]) for i in range(32)]
        self.assertEqual(solve(projects, [16, 16, 16]),
                         dict(selected=['p%02d' % i for i in range(16)], value=16, cost=[16] * 3))
        projects = [project('p%02d' % i, 0, [0]) for i in range(32)]
        self.assertEqual(solve(projects, [0], ['p31']),
                         dict(selected=['p%02d' % i for i in range(32)], value=0, cost=[0]))

    def test_32_independent_against_cost_dynamic_program(self):
        rng = random.Random(743)
        for dims in (1, 2, 3):
            budget = [8] * dims
            projects = [project('p%02d' % i, rng.randrange(-2, 9),
                                [rng.randrange(1, 5) for _ in range(dims)]) for i in range(32)]
            # Every cost is positive, so equal-cost sets cannot be prefixes;
            # numeric bits in reverse ID order express their ID tie-break.
            states = {(0,) * dims: (0, 0)}
            for i, p in enumerate(projects):
                additions = {}
                for cost, (value, bits) in states.items():
                    new_cost = tuple(cost[d] + p['cost'][d] for d in range(dims))
                    if any(new_cost[d] > budget[d] for d in range(dims)):
                        continue
                    new = (value + p['value'], bits | (1 << (31 - i)))
                    if new > max(states.get(new_cost, (-10**9, 0)), additions.get(new_cost, (-10**9, 0))):
                        additions[new_cost] = new
                states.update(additions)
            cost, (value, bits) = min(states.items(), key=lambda pair: (-pair[1][0], pair[0], -pair[1][1]))
            expected = dict(selected=['p%02d' % i for i in range(32) if bits & (1 << (31 - i))],
                            value=value, cost=list(cost))
            with self.subTest(dims=dims):
                self.assertEqual(solve(projects, budget), expected)

    def test_independent_tail_with_required_and_free_ids(self):
        projects = [project('a%02d' % i, 0, [0]) for i in range(4)]
        projects += [project('m%02d' % i, 1, [1]) for i in range(24)]
        projects += [project('z%02d' % i, 0, [0]) for i in range(3)]
        projects += [project('y', -5, [1])]
        self.assertEqual(solve(projects, [5], ('y',)),
                         dict(selected=['a%02d' % i for i in range(4)]
                              + ['m%02d' % i for i in range(4)] + ['y'], value=-1, cost=[5]))

    def test_32_conflicts_and_dependency_chain(self):
        projects = [project('p%02d' % i, 1, [0],
                            excludes=['p%02d' % ((i + 1) % 32)]) for i in range(32)]
        self.assertEqual(solve(projects, [0]),
                         dict(selected=['p%02d' % i for i in range(0, 32, 2)], value=16, cost=[0]))
        projects = [project('p%02d' % i, 1, [1],
                            requires=['p%02d' % (i - 1)] if i else []) for i in range(32)]
        self.assertEqual(solve(projects, [16]),
                         dict(selected=['p%02d' % i for i in range(16)], value=16, cost=[16]))


if __name__ == '__main__':
    unittest.main()
