import copy
import random
import unittest

from optimizer import solve


def brute(projects, budget, required):
    names = [p["id"] for p in projects]
    by_name = {p["id"]: p for p in projects}
    winner = None
    for mask in range(1 << len(projects)):
        chosen = {names[i] for i in range(len(projects)) if mask & (1 << i)}
        if not set(required) <= chosen:
            continue
        if any(not set(by_name[x]["requires"]) <= chosen or
               set(by_name[x]["excludes"]) & chosen for x in chosen):
            continue
        value = sum(by_name[x]["value"] for x in chosen)
        cost = tuple(sum(by_name[x]["cost"][d] for x in chosen)
                     for d in range(len(budget)))
        if any(cost[d] > budget[d] for d in range(len(budget))):
            continue
        ids = tuple(sorted(chosen))
        key = (-value, cost, ids)
        if winner is None or key < winner[0]:
            winner = key, {"selected": list(ids), "value": value,
                           "cost": list(cost)}
    return None if winner is None else winner[1]


class Contract(unittest.TestCase):
    def test_random_small_against_brute_force(self):
        rng = random.Random(20261003)
        for dimensions in (1, 2, 3):
            for linked in (False, True):
                for _ in range(80):
                    n = rng.randrange(0, 10)
                    names = [f"p{i}" for i in range(n)]
                    projects = []
                    for i, name in enumerate(names):
                        dependencies = ([x for x in names[:i]
                                         if rng.randrange(8) == 0]
                                        if linked else [])
                        exclusions = ([x for x in names if x != name
                                       and rng.randrange(12) == 0]
                                      if linked else [])
                        projects.append({"id": name, "value": rng.randrange(-5, 9),
                                         "cost": [rng.randrange(5)
                                                  for _ in range(dimensions)],
                                         "requires": dependencies,
                                         "excludes": exclusions})
                    budget = [rng.randrange(5, 16) for _ in range(dimensions)]
                    required = [x for x in names if rng.randrange(12) == 0]
                    original = copy.deepcopy((projects, budget, required))
                    self.assertEqual(solve(projects, budget, required),
                                     brute(projects, budget, required))
                    self.assertEqual((projects, budget, required), original)

    def test_validation_of_unselected_projects(self):
        good = {"id": "a", "value": 2, "cost": [1],
                "requires": [], "excludes": []}
        bad = {"id": "b", "value": 5, "cost": [99],
               "requires": ["missing"], "excludes": []}
        with self.assertRaises(ValueError):
            solve([good, bad], [1])
        with self.assertRaises(ValueError):
            solve([dict(good, requires=["b"]),
                   dict(bad, requires=["a"])], [1])

    def test_zero_value_zero_cost_id_tie(self):
        projects = [
            {"id": "z", "value": 4, "cost": [1],
             "requires": [], "excludes": []},
            {"id": "a", "value": 0, "cost": [0],
             "requires": [], "excludes": []},
        ]
        self.assertEqual(solve(projects, [1])["selected"], ["a", "z"])


if __name__ == "__main__":
    unittest.main()
