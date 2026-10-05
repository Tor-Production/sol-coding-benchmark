import random
import unittest

from optimizer import solve


class ExactRandom(unittest.TestCase):
    def test_small_instances_against_exhaustive_search(self):
        rng = random.Random(713)
        for _ in range(150):
            n = rng.randrange(0, 9)
            dimensions = rng.randrange(1, 4)
            ids = [f"p{i}" for i in range(n)]
            projects = []
            for i, name in enumerate(ids):
                projects.append({
                    "id": name,
                    "value": rng.randrange(-3, 8),
                    "cost": [rng.randrange(4) for _ in range(dimensions)],
                    "requires": [ids[j] for j in range(i) if rng.randrange(6) == 0],
                    "excludes": [ids[j] for j in range(n) if j != i and rng.randrange(12) == 0],
                })
            budget = [rng.randrange(2, 9) for _ in range(dimensions)]
            required = [name for name in ids if rng.randrange(8) == 0]
            expected = None
            for mask in range(1 << n):
                chosen = {ids[i] for i in range(n) if mask & (1 << i)}
                if not set(required) <= chosen:
                    continue
                if any(not set(p["requires"]) <= chosen or
                       (p["id"] in chosen and set(p["excludes"]) & chosen)
                       for p in projects if p["id"] in chosen):
                    continue
                cost = tuple(sum(projects[i]["cost"][d] for i in range(n) if mask & (1 << i))
                             for d in range(dimensions))
                if any(cost[d] > budget[d] for d in range(dimensions)):
                    continue
                value = sum(projects[i]["value"] for i in range(n) if mask & (1 << i))
                candidate = (-value, cost, tuple(sorted(chosen)))
                if expected is None or candidate < expected:
                    expected = candidate
            answer = solve(projects, budget, required)
            if expected is None:
                self.assertIsNone(answer)
            else:
                self.assertEqual(answer, {"selected": list(expected[2]),
                                          "value": -expected[0], "cost": list(expected[1])})


if __name__ == "__main__":
    unittest.main()
