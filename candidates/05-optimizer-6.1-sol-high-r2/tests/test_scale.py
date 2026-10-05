import random
import unittest

from optimizer import solve
from test_contract import exhaustive, project


class Scale(unittest.TestCase):
    def test_connected_search_against_exhaustive(self):
        rng = random.Random(907)
        items = [project(f'p{i:02}', rng.randrange(-3, 9),
                         [rng.randrange(4), rng.randrange(4)],
                         excludes=[f'p{i+1:02}'] if i < 16 else [])
                 for i in range(17)]
        self.assertEqual(solve(items, [10, 11]), exhaustive(items, [10, 11]))

    def test_cycle_32(self):
        items = [project(f'p{i:02}', 1, [1], excludes=[f'p{(i+1)%32:02}'])
                 for i in range(32)]
        self.assertEqual(solve(items, [32]),
                         dict(selected=[f'p{i:02}' for i in range(0, 32, 2)],
                              value=16, cost=[16]))

    def test_dense_clique_32(self):
        items = [project(f'p{i:02}', 1, [1],
                         excludes=[f'p{j:02}' for j in range(i + 1, 32)])
                 for i in range(32)]
        self.assertEqual(solve(items, [32]),
                         dict(selected=['p00'], value=1, cost=[1]))

    def test_distinct_three_dimensional_costs_32(self):
        # Costs in dimension zero make each value-16 optimum select exactly
        # 16 projects. The smallest total in that dimension uniquely chooses
        # the first 16; the other axes have independent, large integer costs.
        rng = random.Random(28)
        items = [project(f'p{i:02}', 1,
                         [10**9 + i, rng.randrange(10**6), rng.randrange(10**6)])
                 for i in range(32)]
        expected_cost = [sum(p['cost'][d] for p in items[:16]) for d in range(3)]
        budget = [16 * 10**9 + sum(range(32)),
                  sum(p['cost'][1] for p in items),
                  sum(p['cost'][2] for p in items)]
        self.assertEqual(solve(items, budget),
                         dict(selected=[f'p{i:02}' for i in range(16)],
                              value=16, cost=expected_cost))

    def test_many_free_id_ties_32(self):
        items = [project(f'p{i:02}', int(i == 16), [int(i == 16)])
                 for i in range(32)]
        self.assertEqual(solve(items, [1]),
                         dict(selected=[f'p{i:02}' for i in range(17)],
                              value=1, cost=[1]))


if __name__ == '__main__':
    unittest.main()
