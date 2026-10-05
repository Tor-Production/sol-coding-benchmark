import copy
import random
import unittest

from optimizer import solve


def project(name, value=1, cost=(0,), requires=(), excludes=()):
    return dict(id=name, value=value, cost=list(cost),
                requires=list(requires), excludes=list(excludes))


def exhaustive(projects, budget, required=()):
    """Independent specification oracle for small, already valid inputs."""
    best = None
    best_key = None
    for mask in range(1 << len(projects)):
        chosen = [p for i, p in enumerate(projects) if mask & (1 << i)]
        ids = {p['id'] for p in chosen}
        if not set(required) <= ids:
            continue
        if any(not set(p['requires']) <= ids or set(p['excludes']) & ids
               for p in chosen):
            continue
        costs = [sum(p['cost'][d] for p in chosen) for d in range(len(budget))]
        if any(c > b for c, b in zip(costs, budget)):
            continue
        value = sum(p['value'] for p in chosen)
        names = sorted(ids)
        key = (-value, tuple(costs), tuple(names))
        if best_key is None or key < best_key:
            best_key = key
            best = dict(selected=names, value=value, cost=costs)
    return best


class Contract(unittest.TestCase):
    def test_random_instances_against_exhaustive_oracle(self):
        rng = random.Random(73091)
        for case in range(180):
            n = rng.randrange(0, 11)
            dimensions = 1 + case % 3
            names = ['id%02d' % i for i in range(n)]
            items = []
            for i, name in enumerate(names):
                deps = [names[j] for j in range(i) if rng.random() < .14]
                excluded = [names[j] for j in range(n)
                            if j != i and rng.random() < .09]
                items.append(project(name, rng.randrange(-5, 10),
                                     [rng.randrange(0, 5) for _ in range(dimensions)],
                                     deps, excluded))
            rng.shuffle(items)
            budget = [rng.randrange(0, 13) for _ in range(dimensions)]
            required = [name for name in names if rng.random() < .12]
            with self.subTest(case=case):
                self.assertEqual(solve(items, budget, required),
                                 exhaustive(items, budget, required))

    def test_tuple_prefix_ties_and_zero_cost_dependencies(self):
        cases = [
            ([project('a', 0), project('z', 5)], (), ['a', 'z']),
            ([project('z', 0), project('a', 5)], (), ['a']),
            ([project('a', 0), project('b', 0)], (), []),
            ([project('b', 0), project('a', 0)], ['b'], ['a', 'b']),
            ([project('a', 5, requires=['z']), project('z', 0)], (), ['a', 'z']),
            ([project('a', 5), project('b', 2, requires=['c']),
              project('c', -2)], (), ['a']),
        ]
        for items, required, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(solve(items, [0], required)['selected'], expected)

    def test_dimension_order_and_large_integers(self):
        huge = 10 ** 100
        items = [project('a', huge, [1, 0, 4], excludes=['b']),
                 project('b', huge, [0, 9, 0])]
        self.assertEqual(solve(items, [1, 9, 4]),
                         dict(selected=['b'], value=huge, cost=[0, 9, 0]))

    def test_transitive_conflicts_and_infeasible_required(self):
        items = [project('a', 10, [0], requires=['b']),
                 project('b', -3, [0], requires=['c']),
                 project('c', 0, [0], excludes=['a']),
                 project('d', 1, [0])]
        self.assertEqual(solve(items, [0]), dict(selected=['c', 'd'], value=1, cost=[0]))
        self.assertIsNone(solve(items, [0], ['a']))
        self.assertEqual(solve([project('a', -8)], [0], ['a']),
                         dict(selected=['a'], value=-8, cost=[0]))

    def test_no_mutation(self):
        items = [project('z', -1, [1, 2]),
                 project('a', 4, [2, 1], requires=['z'])]
        budget, required = [3, 3], ['a']
        before = copy.deepcopy((items, budget, required))
        solve(items, budget, required)
        self.assertEqual((items, budget, required), before)

    def test_invalid_inputs(self):
        valid = project('a')
        malformed = [None, (), {}, [None], [dict(valid, extra=1)],
                     [{k: v for k, v in valid.items() if k != 'cost'}],
                     [project('')], [project('a'), project('a')],
                     [dict(valid, id=4)], [dict(valid, value=True)],
                     [dict(valid, value=1.5)], [dict(valid, cost=())],
                     [dict(valid, cost=[True])], [dict(valid, cost=[-1])],
                     [dict(valid, cost=[])], [dict(valid, requires=())],
                     [dict(valid, excludes=())], [dict(valid, requires=[[]])],
                     [project('a', requires=['a'])],
                     [project('a', excludes=['a'])],
                     [project('a', requires=['unknown'])],
                     [project('a', excludes=['unknown'])],
                     [project('a', requires=['b', 'b']), project('b')],
                     [project('a', excludes=['b', 'b']), project('b')],
                     [project(str(i)) for i in range(33)]]
        for items in malformed:
            with self.subTest(items=items), self.assertRaises(ValueError):
                solve(items, [1])
        for budget in (None, (), [], [1] * 4, [True], [-1], [1.0]):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                solve([valid], budget)
        for required in (None, 'a', {'a'}, ['a', 'a'], ['unknown'], [[]], [True]):
            with self.subTest(required=required), self.assertRaises(ValueError):
                solve([valid], [1], required)
        # Unaffordable projects are still validated, including their cycles.
        cyclic = [project('a', cost=[100], requires=['b']),
                  project('b', cost=[100], requires=['a'])]
        with self.assertRaises(ValueError):
            solve(cyclic, [0])

    def test_32_project_dependency_components(self):
        items = []
        for i in range(16):
            dep, root = 'd%02d' % i, 'r%02d' % i
            items.extend([project(dep, -1, [0, 1, 0]),
                          project(root, i + 2, [1, 0, 1], requires=[dep])])
        expected = sorted(['d%02d' % i for i in range(8, 16)] +
                          ['r%02d' % i for i in range(8, 16)])
        self.assertEqual(solve(items, [8, 8, 8]),
                         dict(selected=expected, value=sum(range(9, 17)),
                              cost=[8, 8, 8]))

    def test_32_project_connected_conflict_cycle(self):
        items = [project('p%02d' % i, 1, [0],
                         excludes=['p%02d' % ((i + 1) % 32)]) for i in range(32)]
        self.assertEqual(solve(items, [0]),
                         dict(selected=['p%02d' % i for i in range(0, 32, 2)],
                              value=16, cost=[0]))


if __name__ == '__main__':
    unittest.main()
