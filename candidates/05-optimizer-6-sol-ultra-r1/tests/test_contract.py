import copy
import random
import unittest

from optimizer import solve


def project(name, value, cost, requires=(), excludes=()):
    return {"id": name, "value": value, "cost": list(cost),
            "requires": list(requires), "excludes": list(excludes)}


def brute(projects, budget, required=()):
    best = None
    ids = [p["id"] for p in projects]
    for mask in range(1 << len(projects)):
        chosen = {ids[i] for i in range(len(ids)) if mask & (1 << i)}
        if not set(required) <= chosen:
            continue
        if any(not set(p["requires"]) <= chosen or
               set(p["excludes"]) & chosen
               for p in projects if p["id"] in chosen):
            continue
        total_cost = tuple(sum(p["cost"][j] for p in projects
                               if p["id"] in chosen)
                           for j in range(len(budget)))
        if any(total_cost[j] > budget[j] for j in range(len(budget))):
            continue
        value = sum(p["value"] for p in projects if p["id"] in chosen)
        sorted_ids = tuple(sorted(chosen))
        candidate = (value, total_cost, sorted_ids)
        if (best is None or candidate[0] > best[0]
                or (candidate[0] == best[0] and candidate[1:] < best[1:])):
            best = candidate
    if best is None:
        return None
    return {"selected": list(best[2]), "value": best[0],
            "cost": list(best[1])}


class Contract(unittest.TestCase):
    def test_random_exact_and_no_mutation(self):
        rng = random.Random(7362)
        for case in range(500):
            n = rng.randrange(0, 11)
            d = rng.randrange(1, 4)
            ids = [f"p{i}" for i in range(n)]
            projects = []
            for i, name in enumerate(ids):
                projects.append(project(
                    name, rng.randrange(-4, 9),
                    [rng.randrange(0, 5) for _ in range(d)],
                    [ids[j] for j in range(i) if rng.random() < 0.13],
                    [ids[j] for j in range(n) if j != i and rng.random() < 0.1]))
            rng.shuffle(projects)
            budget = [rng.randrange(0, 11) for _ in range(d)]
            required = [name for name in ids if rng.random() < 0.12]
            before = copy.deepcopy((projects, budget, required))
            self.assertEqual(solve(projects, budget, required),
                             brute(projects, budget, required), msg=f"case {case}")
            self.assertEqual((projects, budget, required), before)

    def test_zero_cost_lex_ties(self):
        self.assertEqual(solve([project("a", 0, [0]),
                                project("b", 5, [1]),
                                project("c", 0, [0])], [1]),
                         {"selected": ["a", "b"], "value": 5, "cost": [1]})
        self.assertEqual(solve([project("a", 0, [0])], [0]),
                         {"selected": [], "value": 0, "cost": [0]})

    def test_validation_after_unaffordable_project(self):
        with self.assertRaises(ValueError):
            solve([project("a", 1, [20], requires=["missing"])], [1])
        with self.assertRaises(ValueError):
            solve([project("a", 1, [20], requires=["b"]),
                   project("b", 1, [0], requires=["a"])], [1])
        for changed in (False, 1.0, -1):
            with self.assertRaises(ValueError):
                solve([project("a", 1, [changed])], [1])


if __name__ == "__main__":
    unittest.main()
