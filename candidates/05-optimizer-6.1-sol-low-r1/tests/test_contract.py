import copy
import random
import unittest

from optimizer import solve


class Contract(unittest.TestCase):
    def test_random_against_enumeration(self):
        rng = random.Random(291)
        for _ in range(150):
            n = rng.randrange(9)
            dimensions = rng.randrange(1, 4)
            budget = [rng.randrange(10) for _ in range(dimensions)]
            projects = [dict(id=str(i), value=rng.randrange(-4, 9),
                             cost=[rng.randrange(5) for _ in budget],
                             requires=[str(j) for j in range(i) if rng.random() < .15],
                             excludes=[str(j) for j in range(n) if j != i and rng.random() < .08])
                        for i in range(n)]
            required = [str(i) for i in range(n) if rng.random() < .12]
            original = copy.deepcopy(projects)
            candidates = []
            for mask in range(1 << n):
                chosen = [p for i, p in enumerate(projects) if mask >> i & 1]
                ids = {p['id'] for p in chosen}
                if not set(required) <= ids:
                    continue
                if any(not set(p['requires']) <= ids or set(p['excludes']) & ids for p in chosen):
                    continue
                cost = [sum(p['cost'][k] for p in chosen) for k in range(dimensions)]
                if any(c > b for c, b in zip(cost, budget)):
                    continue
                candidates.append(dict(selected=sorted(ids), value=sum(p['value'] for p in chosen), cost=cost))
            expected = min(candidates, key=lambda x: (-x['value'], x['cost'], x['selected'])) if candidates else None
            self.assertEqual(solve(projects, budget, required), expected)
            self.assertEqual(projects, original)

    def test_structured_32_and_zero_ties(self):
        projects = [dict(id=f'{i:02}', value=1, cost=[1, 1], requires=[], excludes=[]) for i in range(32)]
        self.assertEqual(solve(projects, [16, 16]), dict(selected=[f'{i:02}' for i in range(16)], value=16, cost=[16, 16]))
        for p in projects:
            p['value'] = 0
            p['cost'] = [0, 0]
        self.assertEqual(solve(projects, [0, 0], ['20'])['selected'], [f'{i:02}' for i in range(21)])

    def test_invalid_unselectable_projects(self):
        p = dict(id='a', value=1, cost=[100], requires=['b'], excludes=[])
        q = dict(id='b', value=1, cost=[100], requires=['a'], excludes=[])
        with self.assertRaises(ValueError):
            solve([p, q], [0])
        for budget in ([True], (), [1, -1]):
            with self.assertRaises(ValueError):
                solve([], budget)
