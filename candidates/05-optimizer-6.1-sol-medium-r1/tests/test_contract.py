import copy
import random
import unittest

from optimizer import solve


def project(name, value=0, cost=(0,), requires=(), excludes=()):
    return dict(id=name, value=value, cost=list(cost),
                requires=list(requires), excludes=list(excludes))


def brute(projects, budget, required):
    best = None
    for mask in range(1 << len(projects)):
        chosen = [p for i, p in enumerate(projects) if mask >> i & 1]
        ids = {p['id'] for p in chosen}
        if not set(required) <= ids:
            continue
        if any(not set(p['requires']) <= ids or set(p['excludes']) & ids for p in chosen):
            continue
        cost = tuple(sum(p['cost'][d] for p in chosen) for d in range(len(budget)))
        if any(c > b for c, b in zip(cost, budget)):
            continue
        value = sum(p['value'] for p in chosen)
        key = (-value, cost, tuple(sorted(ids)))
        if best is None or key < best:
            best = key
    if best is None:
        return None
    return dict(selected=list(best[2]), value=-best[0], cost=list(best[1]))


class Contract(unittest.TestCase):
    def test_random_against_exhaustive(self):
        rng = random.Random(19283)
        for case in range(250):
            n = rng.randrange(11)
            dims = rng.randrange(1, 4)
            names = [str(i) for i in range(n)]
            ps = [project(names[i], rng.randrange(-5, 10),
                          [rng.randrange(5) for _ in range(dims)],
                          [names[j] for j in range(i) if rng.random() < .15],
                          [names[j] for j in range(n) if i != j and rng.random() < .08])
                  for i in range(n)]
            rng.shuffle(ps)
            budget = [rng.randrange(12) for _ in range(dims)]
            required = [name for name in names if rng.random() < .12]
            original = copy.deepcopy((ps, budget, required))
            with self.subTest(case=case):
                self.assertEqual(solve(ps, budget, required), brute(ps, budget, required))
                self.assertEqual((ps, budget, required), original)

    def test_zero_value_lexicographic_prefix(self):
        ps = [project('a'), project('b', 1), project('c')]
        self.assertEqual(solve(ps, [0])['selected'], ['a', 'b'])
        self.assertEqual(solve([project('a'), project('b')], [0])['selected'], [])
        self.assertEqual(solve(ps, [0], ['c'])['selected'], ['a', 'b', 'c'])

    def test_uniform_bounds_against_exhaustive(self):
        rng = random.Random(912)
        for case in range(100):
            dims = rng.randrange(1, 4)
            cost = [rng.randrange(3) for _ in range(dims)]
            ps = [project(str(i), 2 if i >= 4 else rng.randrange(-2, 6),
                          cost if i >= 4 else [rng.randrange(3) for _ in range(dims)],
                          [str(j) for j in range(i) if rng.random() < .12],
                          [str(j) for j in range(8) if j != i and rng.random() < .1])
                  for i in range(8)]
            budget = [rng.randrange(9) for _ in range(dims)]
            required = [str(i) for i in range(8) if rng.random() < .05]
            with self.subTest(case=case):
                self.assertEqual(solve(ps, budget, required), brute(ps, budget, required))

    def test_cost_dimension_order(self):
        ps = [project('a', 1, [1, 0]), project('z', 1, [0, 9])]
        self.assertEqual(solve(ps, [1, 9])['selected'], ['a', 'z'])
        ps[0]['excludes'] = ['z']
        self.assertEqual(solve(ps, [1, 9])['selected'], ['z'])

    def test_validate_unusable_projects(self):
        ps = [project('a', cost=[100], requires=['b']),
              project('b', requires=['a'])]
        with self.assertRaises(ValueError):
            solve(ps, [0])
        ps = [project('a', requires=['b'], excludes=['b']), project('b')]
        self.assertIsNone(solve(ps, [0], ['a']))
        self.assertEqual(solve(ps, [0])['selected'], [])

    def test_invalid_inputs(self):
        good = project('a')
        bad_projects = [None, (), [None], [good] * 2, [good] * 33,
                        [dict(good, extra=1)], [dict(good, id='')],
                        [dict(good, value=True)], [dict(good, cost=[False])],
                        [dict(good, cost=(-1,))], [dict(good, requires=())],
                        [dict(good, excludes=['a'])], [dict(good, requires=['x'])],
                        [dict(good, excludes=[[]])],
                        [dict(good, requires=['b', 'b']), project('b')]]
        for ps in bad_projects:
            with self.subTest(projects=ps), self.assertRaises(ValueError):
                solve(ps, [0])
        for budget in [(), [], [0] * 4, [True], [-1], [1.0]]:
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                solve([good], budget)
        for required in [None, {'a'}, ['a', 'a'], ['x'], [[]]]:
            with self.subTest(required=required), self.assertRaises(ValueError):
                solve([good], [0], required)

    def test_32_project_structures(self):
        ps = [project('%02d' % i, 1, [1]) for i in range(32)]
        self.assertEqual(solve(ps, [16]),
                         dict(selected=['%02d' % i for i in range(16)], value=16, cost=[16]))
        ps = [project('%02d' % i, requires=['%02d' % (i - 1)] if i else [])
              for i in range(32)]
        self.assertEqual(solve(ps, [0], ['31'])['selected'], ['%02d' % i for i in range(32)])
        ps = [project('%02d' % i, 1, [1],
                      excludes=['%02d' % (i + 16)] if i < 16 else []) for i in range(32)]
        self.assertEqual(solve(ps, [32]),
                         dict(selected=['%02d' % i for i in range(16)], value=16, cost=[16]))


if __name__ == '__main__':
    unittest.main()
