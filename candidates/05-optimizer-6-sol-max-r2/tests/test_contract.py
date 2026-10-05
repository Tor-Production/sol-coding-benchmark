import copy
import random
import unittest

from optimizer import solve


def project(name, value, cost, requires=(), excludes=()):
    return {"id": name, "value": value, "cost": list(cost),
            "requires": list(requires), "excludes": list(excludes)}


def brute_force(projects, budget, required=()):
    best = None
    n = len(projects)
    required = set(required)
    for mask in range(1 << n):
        picked = {projects[i]["id"] for i in range(n) if mask & (1 << i)}
        if not required <= picked:
            continue
        if any(not set(p["requires"]) <= picked
               or set(p["excludes"]) & picked
               for p in projects if p["id"] in picked):
            continue
        cost = tuple(sum(projects[i]["cost"][d] for i in range(n)
                         if mask & (1 << i)) for d in range(len(budget)))
        if any(cost[d] > budget[d] for d in range(len(budget))):
            continue
        value = sum(projects[i]["value"] for i in range(n) if mask & (1 << i))
        names = tuple(sorted(picked))
        key = (-value, cost, names)
        if best is None or key < best[0]:
            best = key, {"selected": list(names), "value": value,
                         "cost": list(cost)}
    return None if best is None else best[1]


class Contract(unittest.TestCase):
    def test_zero_cost_lexicographic_ties(self):
        rows = [project("a", 0, [0]), project("b", 1, [1]),
                project("c", 1, [1]), project("z", 0, [0])]
        self.assertEqual(solve(rows, [1]),
                         {"selected": ["a", "b"], "value": 1, "cost": [1]})
        self.assertEqual(solve([project("a", 0, [0]),
                                project("z", 0, [0])], [0]),
                         {"selected": [], "value": 0, "cost": [0]})
        self.assertEqual(solve([project("a", 0, [0]),
                                project("z", 0, [0])], [0], ["z"]),
                         {"selected": ["a", "z"], "value": 0, "cost": [0]})

    def test_random_small_against_brute_force(self):
        rng = random.Random(12948)
        for case in range(250):
            n = rng.randrange(0, 10)
            dimensions = rng.randrange(1, 4)
            names = [f"p{i:02d}" for i in range(n)]
            rows = []
            for i, name in enumerate(names):
                requires = [names[j] for j in range(i)
                            if rng.random() < 0.17]
                excludes = [names[j] for j in range(n) if j != i
                            and rng.random() < 0.08]
                rows.append(project(name, rng.randrange(-5, 9),
                                    [rng.randrange(4) for _ in range(dimensions)],
                                    requires, excludes))
            rng.shuffle(rows)
            budget = [rng.randrange(0, 11) for _ in range(dimensions)]
            required = [name for name in names if rng.random() < 0.12]
            original = copy.deepcopy((rows, budget, required))
            self.assertEqual(solve(rows, budget, required),
                             brute_force(rows, budget, required), case)
            self.assertEqual((rows, budget, required), original)

    def test_zero_value_constraints_against_brute_force(self):
        rng = random.Random(3814)
        for case in range(500):
            n = rng.randrange(1, 10)
            names = [f"p{i:02d}" for i in range(n)]
            rows = [project(name, rng.choice([-1, 0]), [rng.randrange(2)],
                            [names[j] for j in range(i)
                             if rng.random() < 0.16],
                            [names[j] for j in range(n) if j != i
                             and rng.random() < 0.16])
                    for i, name in enumerate(names)]
            required = [name for name in names if rng.random() < 0.15]
            budget = [rng.randrange(3)]
            self.assertEqual(solve(rows, budget, required),
                             brute_force(rows, budget, required), case)

    def test_interleaved_components(self):
        rows = [project("a", 2, [1], excludes=["c"]),
                project("b", 2, [1], excludes=["d"]),
                project("c", 2, [1]), project("d", 2, [1])]
        self.assertEqual(solve(rows, [2]), brute_force(rows, [2]))

    def test_invalid_inputs_even_when_unaffordable(self):
        bad_cases = [
            ([project("a", 1, [100], requires=["missing"])], [0], ()),
            ([project("a", 1, [100], requires=["b"]),
              project("b", 1, [100], requires=["a"])], [0], ()),
            ([project("a", 1, [0], excludes=["a"])], [0], ()),
            ([project("a", 1, [0], requires=["b", "b"]),
              project("b", 1, [0])], [0], ()),
            ([project("a", 1, [0])], [0], ["unknown"]),
            ([project("a", 1, [0])], [0], ["a", "a"]),
            ([project("a", 1, [False])], [0], ()),
            ([project("a", 1, [0])], [True], ()),
        ]
        for rows, budget, required in bad_cases:
            with self.subTest(rows=rows, budget=budget, required=required):
                with self.assertRaises(ValueError):
                    solve(rows, budget, required)


if __name__ == "__main__":
    unittest.main()
