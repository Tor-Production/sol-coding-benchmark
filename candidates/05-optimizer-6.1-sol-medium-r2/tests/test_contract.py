import copy
import itertools
import random
import unittest

from optimizer import solve


def project(name, value=0, cost=(0,), requires=(), excludes=()):
    return dict(id=name, value=value, cost=list(cost),
                requires=list(requires), excludes=list(excludes))


def exhaustive(projects, budget, required=()):
    best = None
    answer = None
    for flags in itertools.product((False, True), repeat=len(projects)):
        chosen = [p for p, flag in zip(projects, flags) if flag]
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
        key = (-value, tuple(costs), tuple(sorted(ids)))
        if best is None or key < best:
            best = key
            answer = dict(selected=sorted(ids), value=value, cost=costs)
    return answer


class Contract(unittest.TestCase):
    def test_random_against_exhaustive(self):
        rng = random.Random(91823)
        for case in range(400):
            n = rng.randrange(10)
            dims = rng.randrange(1, 4)
            names = [str(i) for i in range(n)]
            projects = [project(name, rng.randrange(-5, 10),
                                [rng.randrange(5) for _ in range(dims)],
                                [x for x in names[:i] if rng.random() < .17],
                                [x for x in names if x != name and rng.random() < .10])
                        for i, name in enumerate(names)]
            rng.shuffle(projects)
            budget = [rng.randrange(12) for _ in range(dims)]
            required = [x for x in names if rng.random() < .08]
            with self.subTest(case=case):
                self.assertEqual(solve(projects, budget, required),
                                 exhaustive(projects, budget, required))

    def test_zero_value_id_ties_and_no_mutation(self):
        projects = [project('b', 1), project('a'), project('c')]
        original = copy.deepcopy(projects)
        self.assertEqual(solve(projects, [0]),
                         dict(selected=['a', 'b'], value=1, cost=[0]))
        self.assertEqual(projects, original)
        self.assertEqual(solve([project('a')], [0])['selected'], [])

    def test_valid_but_impossible_dependency(self):
        projects = [project('a', 10, requires=['b'], excludes=['b']),
                    project('b', 1)]
        self.assertEqual(solve(projects, [0])['selected'], ['b'])
        self.assertIsNone(solve(projects, [0], ['a']))
        projects = [project('a', -1), project('b', -2, requires=['a'])]
        self.assertEqual(solve(projects, [0], ('b',)),
                         dict(selected=['a', 'b'], value=-3, cost=[0]))

    def test_structured_32(self):
        projects = [project('%02d' % i, 1, [1, 2, 3]) for i in range(32)]
        self.assertEqual(solve(projects, [16, 32, 48]),
                         dict(selected=['%02d' % i for i in range(16)],
                              value=16, cost=[16, 32, 48]))
        chain = [project(str(i), -1 if i < 31 else 100, [1],
                         [str(i - 1)] if i else []) for i in range(32)]
        self.assertEqual(solve(chain, [32])['value'], 69)

    def test_validation_all_projects(self):
        bad_projects = [
            [project('a', cost=[True])],
            [project('a', requires=['a'])],
            [project('a', excludes=['missing'])],
            [project('a', requires=['b', 'b']), project('b')],
            [project('a', requires=['b']), project('b', requires=['a'])],
            [project('a'), project('a')],
            [dict(project('a'), extra=0)],
            [dict(project('a'), requires=())],
            [project('a', cost=[999], requires=['missing'])],
        ]
        for projects in bad_projects:
            with self.subTest(projects=projects), self.assertRaises(ValueError):
                solve(projects, [0])
        for budget in ((), [], [True], [-1], [0, 0, 0, 0]):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                solve([], budget)
        for required in ('a', ['missing'], ['a', 'a'], [False]):
            with self.subTest(required=required), self.assertRaises(ValueError):
                solve([project('a')], [0], required)

    def test_large_integers_and_cost_order(self):
        huge = 10 ** 100
        projects = [project('a', huge, [1, 0]), project('z', huge, [0, 9])]
        self.assertEqual(solve(projects, [1, 9], ['a'])['value'], 2 * huge)
        self.assertEqual(solve(projects, [0, 9])['selected'], ['z'])
        projects = [project('a', 2, [1, 0], excludes=['z']),
                    project('z', 2, [0, 9])]
        self.assertEqual(solve(projects, [1, 9])['selected'], ['z'])


if __name__ == '__main__':
    unittest.main()
