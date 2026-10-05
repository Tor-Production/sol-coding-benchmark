import random
import unittest

from optimizer import solve


class Exactness(unittest.TestCase):
    def test_random_small_against_enumeration(self):
        rng = random.Random(1729)
        for _ in range(100):
            n = rng.randrange(1, 9)
            dims = rng.randrange(1, 4)
            budget = [rng.randrange(0, 10) for _ in range(dims)]
            projects = []
            for i in range(n):
                projects.append({
                    "id": f"p{i}",
                    "value": rng.randrange(-4, 10),
                    "cost": [rng.randrange(0, 5) for _ in range(dims)],
                    "requires": [f"p{j}" for j in range(i)
                                 if rng.randrange(8) == 0],
                    "excludes": [f"p{j}" for j in range(n) if j != i
                                 and rng.randrange(12) == 0],
                })
            required = [f"p{i}" for i in range(n) if rng.randrange(9) == 0]
            expected = None
            for mask in range(1 << n):
                chosen = {projects[i]["id"] for i in range(n) if mask >> i & 1}
                if not set(required) <= chosen:
                    continue
                if any(not set(p["requires"]) <= chosen or
                       set(p["excludes"]) & chosen
                       for p in projects if p["id"] in chosen):
                    continue
                cost = tuple(sum(projects[i]["cost"][d] for i in range(n)
                                 if mask >> i & 1) for d in range(dims))
                if any(cost[d] > budget[d] for d in range(dims)):
                    continue
                value = sum(projects[i]["value"] for i in range(n)
                            if mask >> i & 1)
                ids = tuple(sorted(chosen))
                key = (-value, cost, ids)
                if expected is None or key < expected[0]:
                    expected = (key, {"selected": list(ids), "value": value,
                                      "cost": list(cost)})
            self.assertEqual(solve(projects, budget, required),
                             None if expected is None else expected[1])

    def test_uniform_32_tie(self):
        projects = [{"id": f"p{i:02}", "value": 1, "cost": [1],
                     "requires": [], "excludes": []} for i in range(32)]
        self.assertEqual(solve(projects, [16]), {
            "selected": [f"p{i:02}" for i in range(16)],
            "value": 16, "cost": [16],
        })

    def test_large_integer_and_transitive_conflict(self):
        huge = 10 ** 400
        projects = [
            {"id": "a", "value": huge, "cost": [huge],
             "requires": ["b"], "excludes": []},
            {"id": "b", "value": -1, "cost": [0],
             "requires": [], "excludes": ["c"]},
            {"id": "c", "value": huge, "cost": [huge],
             "requires": [], "excludes": []},
        ]
        self.assertEqual(solve(projects, [huge]),
                         {"selected": ["c"], "value": huge,
                          "cost": [huge]})
        self.assertIsNone(solve(projects, [2 * huge], ["a", "c"]))


if __name__ == "__main__":
    unittest.main()
