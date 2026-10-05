import copy
import random
import unittest

from optimizer import solve


def project(name, value, cost, requires=(), excludes=()):
    return dict(id=name, value=value, cost=list(cost),
                requires=list(requires), excludes=list(excludes))


def exhaustive(projects, budget, required=()):
    """Small-instance oracle from the contract, without solver internals."""
    best = None
    answer = None
    for mask in range(1 << len(projects)):
        chosen = [p for i, p in enumerate(projects) if mask >> i & 1]
        ids = {p['id'] for p in chosen}
        if not set(required) <= ids:
            continue
        if any(not set(p['requires']) <= ids or set(p['excludes']) & ids
               for p in chosen):
            continue
        cost = tuple(sum(p['cost'][d] for p in chosen) for d in range(len(budget)))
        if any(a > b for a, b in zip(cost, budget)):
            continue
        value = sum(p['value'] for p in chosen)
        names = tuple(sorted(ids))
        key = (-value, cost, names)
        if best is None or key < best:
            best = key
            answer = dict(selected=list(names), value=value, cost=list(cost))
    return answer


class Contract(unittest.TestCase):
    def test_random_against_exhaustive(self):
        rng = random.Random(70491)
        for case in range(240):
            dimensions = rng.randrange(1, 4)
            count = rng.randrange(0, 11)
            names = [f'p{i}' for i in range(count)]
            items = []
            for i, name in enumerate(names):
                needs = [ref for ref in names[:i] if rng.random() < .17]
                excludes = [ref for ref in names if ref != name and rng.random() < .1]
                items.append(project(name, rng.randrange(-5, 10),
                                     [rng.randrange(5) for _ in range(dimensions)],
                                     needs, excludes))
            rng.shuffle(items)
            budget = [rng.randrange(12) for _ in range(dimensions)]
            required = [name for name in names if rng.random() < .08]
            with self.subTest(case=case):
                self.assertEqual(solve(items, budget, required),
                                 exhaustive(items, budget, required))

    def test_free_zero_value_id_prefixes(self):
        items = [project('a', 0, [0]), project('b', 0, [0]),
                 project('m', 4, [1]), project('z', 0, [0])]
        self.assertEqual(solve(items, [1]),
                         dict(selected=['a', 'b', 'm'], value=4, cost=[1]))
        self.assertEqual(solve(items, [0]), dict(selected=[], value=0, cost=[0]))
        self.assertEqual(solve(items, [0], ['z']),
                         dict(selected=['a', 'b', 'z'], value=0, cost=[0]))

    def test_random_free_id_ties_against_exhaustive(self):
        rng = random.Random(184)
        # Frequent equal value/cost choices exercise prefix ties when
        # independent components and mandatory IDs interleave in ID order.
        names = ['A', 'aa', 'b', 'ba', 'q', 'z', '\u00e9', '\u03b1', '\u4e2d']
        for case in range(80):
            items = [project(name, rng.randrange(2), [0, 0],
                             [ref for ref in names[:i] if rng.random() < .1],
                             [ref for ref in names[i+1:] if rng.random() < .15])
                     for i, name in enumerate(names)]
            rng.shuffle(items)
            required = [name for name in names if rng.random() < .1]
            with self.subTest(case=case):
                self.assertEqual(solve(items, [0, 0], required),
                                 exhaustive(items, [0, 0], required))

    def test_interleaved_components_id_ties(self):
        items = [project('a', 0, [0], excludes=['c']),
                 project('b', 0, [0], excludes=['d']),
                 project('c', 0, [0]), project('d', 0, [0]),
                 project('z', 10, [1])]
        self.assertEqual(solve(items, [1]), exhaustive(items, [1]))
        self.assertEqual(solve(items, [1], ['d']), exhaustive(items, [1], ['d']))

    def test_dimension_order_and_large_integers(self):
        huge = 10 ** 100
        items = [project('a', huge, [2, 0, 1]),
                 project('z', huge, [1, 2, 1])]
        self.assertEqual(solve(items, [2, 2, 1]),
                         dict(selected=['z'], value=huge, cost=[1, 2, 1]))

    def test_transitive_shared_dependencies_and_conflict(self):
        items = [project('a', 9, [2], ['b', 'c']),
                 project('b', 3, [1], ['d']),
                 project('c', 3, [1], ['d']), project('d', -4, [1]),
                 project('e', 10, [3], excludes=['d'])]
        self.assertEqual(solve(items, [5]), exhaustive(items, [5]))
        self.assertIsNone(solve(items, [20], ['a', 'e']))
        self.assertIsNone(solve([project('a', 5, [0], ['b'], ['b']),
                                 project('b', 0, [0])], [0], ['a']))

    def test_inputs_not_mutated(self):
        items = [project('z', 2, [1, 0], ['a']), project('a', -1, [0, 1])]
        budget = [3, 3]
        required = ['z']
        before = copy.deepcopy((items, budget, required))
        solve(items, budget, required)
        self.assertEqual((items, budget, required), before)

    def test_validation(self):
        valid = project('a', 1, [1])
        cases = [
            ((), [1], ()),
            ([valid] * 33, [1], ()),
            ([valid], (), ()),
            ([valid], [], ()),
            ([valid], [1, 1, 1, 1], ()),
            ([valid], [True], ()),
            ([valid], [-1], ()),
            ([valid], [1.0], ()),
            ([valid], [1], {'a'}),
            ([valid], [1], ['a', 'a']),
            ([valid], [1], ['missing']),
            ([valid], [1], [[]]),
            ([valid, valid], [1], ()),
            ([dict(valid, extra=1)], [1], ()),
            ([{k: v for k, v in valid.items() if k != 'excludes'}], [1], ()),
            ([dict(valid, id='')], [1], ()),
            ([dict(valid, id=[])], [1], ()),
            ([dict(valid, value=True)], [1], ()),
            ([dict(valid, value=1.0)], [1], ()),
            ([dict(valid, cost=(1,))], [1], ()),
            ([dict(valid, cost=[True])], [1], ()),
            ([dict(valid, cost=[-1])], [1], ()),
            ([dict(valid, cost=[1, 2])], [1], ()),
            ([dict(valid, requires=())], [1], ()),
            ([dict(valid, excludes='b')], [1], ()),
            ([dict(valid, requires=['a'])], [1], ()),
            ([dict(valid, excludes=['a'])], [1], ()),
            ([dict(valid, requires=['missing'])], [1], ()),
            ([dict(valid, excludes=[[]])], [1], ()),
            ([project('a', 1, [100], ['b', 'b']), project('b', 1, [1])], [1], ()),
            ([project('a', 1, [100], excludes=['b', 'b']), project('b', 1, [1])], [1], ()),
            ([project('a', 1, [100], ['b']), project('b', 1, [100], ['a'])], [1], ()),
        ]
        for items, budget, required in cases:
            with self.subTest(items=items, budget=budget, required=required):
                with self.assertRaises(ValueError):
                    solve(items, budget, required)

    def test_scale_independent_32(self):
        items = [project(f'p{i:02}', i + 1, [1, 1, 1]) for i in range(32)]
        self.assertEqual(solve(items, [16, 16, 16]),
                         dict(selected=[f'p{i:02}' for i in range(16, 32)],
                              value=sum(range(17, 33)), cost=[16, 16, 16]))

    def test_scale_shared_dependency_32(self):
        items = [project('root', -10, [1])]
        items += [project(f'p{i:02}', 2, [1], ['root']) for i in range(31)]
        self.assertEqual(solve(items, [11]),
                         dict(selected=[f'p{i:02}' for i in range(10)] + ['root'],
                              value=10, cost=[11]))

    def test_scale_chain_32(self):
        items = [project(f'p{i:02}', 1, [1], [f'p{i-1:02}'] if i else [])
                 for i in range(32)]
        self.assertEqual(solve(items, [20]),
                         dict(selected=[f'p{i:02}' for i in range(20)],
                              value=20, cost=[20]))

    def test_scale_exclusion_pairs_32(self):
        items = [project(f'p{i:02}', 1, [1], excludes=[f'p{i+1:02}'] if i % 2 == 0 else [])
                 for i in range(32)]
        self.assertEqual(solve(items, [16]),
                         dict(selected=[f'p{i:02}' for i in range(0, 32, 2)],
                              value=16, cost=[16]))


if __name__ == '__main__':
    unittest.main()
