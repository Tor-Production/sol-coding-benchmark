import random
import unittest

from optimizer import solve


def brute_force(projects, budget, required):
    names = sorted(p["id"] for p in projects)
    by_id = {p["id"]: p for p in projects}
    best = None
    for mask in range(1 << len(names)):
        chosen = tuple(name for i, name in enumerate(names) if mask & (1 << i))
        members = set(chosen)
        if not set(required) <= members:
            continue
        if any(not set(by_id[name]["requires"]) <= members or
               set(by_id[name]["excludes"]) & members for name in chosen):
            continue
        cost = tuple(sum(by_id[name]["cost"][d] for name in chosen)
                     for d in range(len(budget)))
        if any(cost[d] > budget[d] for d in range(len(budget))):
            continue
        value = sum(by_id[name]["value"] for name in chosen)
        key = (-value, cost, chosen)
        if best is None or key < best[0]:
            best = (key, {"selected": list(chosen), "value": value,
                          "cost": list(cost)})
    return None if best is None else best[1]


class ContractCases(unittest.TestCase):
    def test_random_small_against_exhaustive(self):
        rng = random.Random(4417)
        for _ in range(100):
            count = rng.randrange(9)
            dimensions = rng.randrange(1, 4)
            budget = [rng.randrange(9) for _ in range(dimensions)]
            projects = []
            for i in range(count):
                projects.append({
                    "id": f"p{i}",
                    "value": rng.randrange(-5, 11),
                    "cost": [rng.randrange(7) for _ in range(dimensions)],
                    "requires": [f"p{j}" for j in range(i)
                                 if rng.randrange(8) == 0],
                    "excludes": [f"p{j}" for j in range(count) if j != i
                                 and rng.randrange(10) == 0],
                })
            required = [f"p{i}" for i in range(count)
                        if rng.randrange(9) == 0]
            snapshot = repr((projects, budget, required))
            self.assertEqual(solve(projects, budget, required),
                             brute_force(projects, budget, required))
            self.assertEqual(repr((projects, budget, required)), snapshot)

    def test_zero_value_lex_tie(self):
        projects = [
            {"id": "z", "value": -1, "cost": [0],
             "requires": [], "excludes": []},
            {"id": "a", "value": 0, "cost": [0],
             "requires": [], "excludes": []},
        ]
        self.assertEqual(solve(projects, [0], ["z"]),
                         {"selected": ["a", "z"], "value": -1, "cost": [0]})
        self.assertEqual(solve(projects, [0]),
                         {"selected": [], "value": 0, "cost": [0]})

    def test_validate_unusable_projects(self):
        bad = [{"id": "x", "value": 1, "cost": [999],
                "requires": ["missing"], "excludes": []}]
        with self.assertRaises(ValueError):
            solve(bad, [0])


if __name__ == "__main__":
    unittest.main()
