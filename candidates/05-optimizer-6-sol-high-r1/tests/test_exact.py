import random
import unittest

from optimizer import solve


def project(name, value, cost, requires=(), excludes=()):
    return {"id": name, "value": value, "cost": list(cost),
            "requires": list(requires), "excludes": list(excludes)}


def brute(projects, budget, required=()):
    best = None
    for mask in range(1 << len(projects)):
        chosen = {p["id"] for i, p in enumerate(projects) if mask & (1 << i)}
        if not set(required) <= chosen:
            continue
        if any(not set(p["requires"]) <= chosen or set(p["excludes"]) & chosen
               for p in projects if p["id"] in chosen):
            continue
        cost = tuple(sum(p["cost"][d] for p in projects if p["id"] in chosen)
                     for d in range(len(budget)))
        if any(x > cap for x, cap in zip(cost, budget)):
            continue
        value = sum(p["value"] for p in projects if p["id"] in chosen)
        names = tuple(sorted(chosen))
        key = (-value, cost, names)
        if best is None or key < best[0]:
            best = (key, {"selected": list(names), "value": value, "cost": list(cost)})
    return None if best is None else best[1]


class ExactContract(unittest.TestCase):
    def test_random_small_against_exhaustive(self):
        rng = random.Random(20261002)
        for _ in range(200):
            n = rng.randrange(1, 10)
            dims = rng.randrange(1, 4)
            projects = []
            for i in range(n):
                dependencies = [f"p{j}" for j in range(i) if rng.random() < .13]
                conflicts = [f"p{j}" for j in range(n) if j != i and rng.random() < .06]
                projects.append(project(f"p{i}", rng.randrange(-5, 10),
                                        [rng.randrange(5) for _ in range(dims)],
                                        dependencies, conflicts))
            budget = [rng.randrange(0, 12) for _ in range(dims)]
            required = [f"p{i}" for i in range(n) if rng.random() < .1]
            with self.subTest(projects=projects, budget=budget, required=required):
                self.assertEqual(solve(projects, budget, required),
                                 brute(projects, budget, required))

    def test_validation_of_unaffordable_project(self):
        with self.assertRaises(ValueError):
            solve([project("a", 1, [99], requires=["missing"])], [0])

    def test_input_unchanged(self):
        projects = [project("a", 4, [2], excludes=["b"]), project("b", 3, [1])]
        original = [dict(p, cost=p["cost"][:], requires=p["requires"][:],
                         excludes=p["excludes"][:]) for p in projects]
        solve(projects, [2])
        self.assertEqual(projects, original)

    def test_zero_cost_id_tie(self):
        projects = [project("z", 5, [0]), project("a", 0, [0]),
                    project("y", 0, [1])]
        self.assertEqual(solve(projects, [1]),
                         {"selected": ["a", "z"], "value": 5, "cost": [0]})

    def test_required_conflict_and_cycle(self):
        projects = [project("a", 2, [0], requires=["b"]),
                    project("b", 1, [0], excludes=["a"])]
        self.assertIsNone(solve(projects, [0], ["a"]))
        with self.assertRaises(ValueError):
            solve([project("a", 1, [0], requires=["b"]),
                   project("b", 1, [0], requires=["a"])], [0])

    def test_structured_32_projects(self):
        projects = []
        for i in range(16):
            projects.append(project(f"base{i:02}", -1, [1]))
            projects.append(project(f"gain{i:02}", 3, [1],
                                    requires=[f"base{i:02}"]))
        answer = solve(projects, [16])
        self.assertEqual(answer["value"], 16)
        self.assertEqual(answer["cost"], [16])
        self.assertEqual(len(answer["selected"]), 16)


if __name__ == "__main__":
    unittest.main()
