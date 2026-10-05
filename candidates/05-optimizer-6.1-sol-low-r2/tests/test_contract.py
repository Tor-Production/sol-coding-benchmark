import itertools
import random
import unittest
from optimizer import solve


class Contract(unittest.TestCase):
    def test_random_against_exhaustive(self):
        rng = random.Random(812)
        for case in range(180):
            n = rng.randrange(9)
            d = rng.randrange(1, 4)
            budget = [rng.randrange(8) for _ in range(d)]
            projects = []
            for i in range(n):
                projects.append(dict(id=str(i), value=rng.randrange(-3, 7),
                    cost=[rng.randrange(4) for _ in range(d)],
                    requires=[str(j) for j in range(i) if case % 2 and rng.random() < .16],
                    excludes=[str(j) for j in range(i) if case % 2 and rng.random() < .16]))
            required = [str(i) for i in range(n) if rng.random() < .1]
            best = None
            for flags in itertools.product((False, True), repeat=n):
                chosen = {str(i) for i in range(n) if flags[i]}
                if not set(required) <= chosen:
                    continue
                if any(not set(p['requires']) <= chosen or set(p['excludes']) & chosen
                       for p in projects if p['id'] in chosen):
                    continue
                cost = [sum(p['cost'][k] for p in projects if p['id'] in chosen) for k in range(d)]
                if any(x > b for x, b in zip(cost, budget)):
                    continue
                value = sum(p['value'] for p in projects if p['id'] in chosen)
                key = (-value, tuple(cost), tuple(sorted(chosen)))
                if best is None or key < best:
                    best = key
            expected = None if best is None else dict(selected=list(best[2]), value=-best[0], cost=list(best[1]))
            self.assertEqual(solve(projects, budget, required), expected, case)

    def test_invalid_unaffordable_cycle(self):
        projects = [dict(id='a', value=1, cost=[100], requires=['b'], excludes=[]),
                    dict(id='b', value=1, cost=[100], requires=['a'], excludes=[])]
        with self.assertRaises(ValueError):
            solve(projects, [0])

    def test_zero_id_ties(self):
        projects = [dict(id=s, value=v, cost=[0], requires=[], excludes=[])
                    for s, v in [('a', 0), ('z', 0), ('m', 1)]]
        self.assertEqual(solve(projects, [0])['selected'], ['a', 'm'])
