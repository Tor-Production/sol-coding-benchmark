import copy
import random
import unittest

from optimizer import solve


def brute(projects, budget, required):
    best = None
    best_key = None
    for mask in range(1 << len(projects)):
        chosen = {p["id"] for i, p in enumerate(projects) if mask & (1 << i)}
        if not set(required) <= chosen:
            continue
        if any(not set(p["requires"]) <= chosen or set(p["excludes"]) & chosen
               for p in projects if p["id"] in chosen):
            continue
        cost = tuple(sum(p["cost"][d] for p in projects if p["id"] in chosen)
                     for d in range(len(budget)))
        if any(cost[d] > budget[d] for d in range(len(budget))):
            continue
        value = sum(p["value"] for p in projects if p["id"] in chosen)
        selected = tuple(sorted(chosen))
        key = (-value, cost, selected)
        if best_key is None or key < best_key:
            best_key = key
            best = {"selected": list(selected), "value": value, "cost": list(cost)}
    return best


class ContractExtra(unittest.TestCase):
    def test_random_against_exhaustive(self):
        rng = random.Random(41957)
        for _ in range(1000):
            n = rng.randrange(0, 11)
            dims = rng.randrange(1, 4)
            budget = [rng.randrange(0, 13) for _ in range(dims)]
            projects = []
            for i in range(n):
                projects.append({
                    "id": f"p{i:02}", "value": rng.randrange(-4, 9),
                    "cost": [rng.randrange(0, 8) for _ in range(dims)],
                    "requires": [f"p{j:02}" for j in range(i)
                                 if rng.randrange(10) == 0],
                    "excludes": [f"p{j:02}" for j in range(n) if j != i
                                 and rng.randrange(15) == 0],
                })
            required = [p["id"] for p in projects if rng.randrange(12) == 0]
            original = copy.deepcopy((projects, budget, required))
            self.assertEqual(solve(projects, budget, required), brute(projects, budget, required))
            self.assertEqual((projects, budget, required), original)

    def test_required_changes_lex_tie(self):
        projects = [
            dict(id="z", value=0, cost=[0], requires=[], excludes=[]),
            dict(id="a", value=0, cost=[0], requires=[], excludes=[]),
        ]
        self.assertEqual(solve(projects, [0], ["z"])["selected"], ["a", "z"])

    def test_independent_random_against_exhaustive(self):
        rng = random.Random(842)
        for _ in range(140):
            n = rng.randrange(0, 13)
            dims = rng.randrange(1, 4)
            projects = [dict(id=f"p{i:02}", value=rng.randrange(-3, 6),
                             cost=[rng.randrange(0, 6) for _ in range(dims)],
                             requires=[], excludes=[]) for i in range(n)]
            budget = [rng.randrange(0, 15) for _ in range(dims)]
            required = [p["id"] for p in projects if rng.randrange(8) == 0]
            self.assertEqual(solve(projects, budget, required), brute(projects, budget, required))

    def test_invalid_unused_projects(self):
        projects = [
            dict(id="a", value=1, cost=[999], requires=["b"], excludes=[]),
            dict(id="b", value=1, cost=[0], requires=["a"], excludes=[]),
        ]
        with self.assertRaises(ValueError):
            solve(projects, [0])
        with self.assertRaises(ValueError):
            solve([dict(id="x", value=1, cost=[True], requires=[], excludes=[])], [1])


if __name__ == "__main__":
    unittest.main()
