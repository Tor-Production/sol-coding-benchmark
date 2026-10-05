import copy
import random
import unittest

from optimizer import solve


def project(name, value, cost, requires=(), excludes=()):
    return {"id": name, "value": value, "cost": list(cost),
            "requires": list(requires), "excludes": list(excludes)}


def exhaustive(projects, budget, required):
    best = None
    for mask in range(1 << len(projects)):
        chosen = {p["id"] for i, p in enumerate(projects) if mask & (1 << i)}
        if not set(required) <= chosen:
            continue
        if any(any(r not in chosen for r in p["requires"])
               or any(x in chosen for x in p["excludes"])
               for p in projects if p["id"] in chosen):
            continue
        cost = [sum(p["cost"][d] for p in projects if p["id"] in chosen)
                for d in range(len(budget))]
        if any(a > b for a, b in zip(cost, budget)):
            continue
        value = sum(p["value"] for p in projects if p["id"] in chosen)
        selected = sorted(chosen)
        candidate = (value, tuple(cost), tuple(selected))
        if (best is None or value > best[0]
                or (value == best[0] and (tuple(cost), tuple(selected))
                    < (best[1], best[2]))):
            best = candidate
    if best is None:
        return None
    return {"selected": list(best[2]), "value": best[0],
            "cost": list(best[1])}


class Contract(unittest.TestCase):
    def test_random_small_against_exhaustive(self):
        rng = random.Random(3419)
        for case in range(250):
            n = rng.randrange(0, 10)
            dim = rng.randrange(1, 4)
            names = [f"p{i:02}" for i in range(n)]
            projects = []
            for i, name in enumerate(names):
                req = [names[j] for j in range(i)
                       if rng.random() < 0.12]
                exc = [names[j] for j in range(n) if j != i
                       and rng.random() < 0.05]
                projects.append(project(name, rng.randrange(-5, 9),
                                        [rng.randrange(0, 5) for _ in range(dim)],
                                        req, exc))
            rng.shuffle(projects)
            budget = [rng.randrange(0, 13) for _ in range(dim)]
            required = [name for name in names if rng.random() < 0.12]
            original = copy.deepcopy((projects, budget, required))
            with self.subTest(case=case):
                self.assertEqual(solve(projects, budget, required),
                                 exhaustive(projects, budget, required))
                self.assertEqual((projects, budget, required), original)

    def test_lex_ties_with_required_and_zero_cost(self):
        projects = [project("a", 0, [0]), project("b", 0, [0]),
                    project("z", 1, [0])]
        self.assertEqual(solve(projects, [0], ["z"])["selected"],
                         ["a", "b", "z"])
        self.assertEqual(solve(projects, [0])["selected"],
                         ["a", "b", "z"])
        self.assertEqual(solve([project("a", 0, [0])], [0])["selected"], [])

    def test_random_tie_heavy_against_exhaustive(self):
        rng = random.Random(9642)
        for case in range(220):
            n = rng.randrange(1, 11)
            dim = rng.randrange(1, 4)
            names = [f"p{i:02}" for i in range(n)]
            projects = []
            for i, name in enumerate(names):
                projects.append(project(
                    name, rng.randrange(-2, 4),
                    [rng.randrange(0, 3) for _ in range(dim)],
                    [names[j] for j in range(i) if rng.random() < 0.08],
                    [names[j] for j in range(n) if j != i
                     and rng.random() < 0.04]))
            budget = [rng.randrange(0, 6) for _ in range(dim)]
            required = [name for name in names if rng.random() < 0.1]
            with self.subTest(case=case):
                self.assertEqual(solve(projects, budget, required),
                                 exhaustive(projects, budget, required))

    def test_validation_after_unaffordable_project(self):
        projects = [project("a", 1, [999]), project("b", 1, [0], ["missing"])]
        with self.assertRaises(ValueError):
            solve(projects, [1])
        with self.assertRaises(ValueError):
            solve([project("a", 1, [0], ["b"]),
                   project("b", 1, [0], ["a"])], [1])
        with self.assertRaises(ValueError):
            solve([project("a", 1, [0])], [1], ["a", "a"])

    def test_disjoint_negative_dependencies(self):
        projects = []
        for i in range(16):
            projects.append(project(f"a{i:02}", -1, [0]))
            projects.append(project(f"b{i:02}", 1, [0], [f"a{i:02}"]))
        self.assertEqual(solve(projects, [0]),
                         {"selected": [], "value": 0, "cost": [0]})

    def test_malformed_inputs(self):
        good = project("a", 1, [0])
        bad_projects = [
            (good,),
            [dict(good, extra=0)],
            [{key: value for key, value in good.items() if key != "cost"}],
            [project("a", True, [0])],
            [project("a", 1, [True])],
            [dict(good, cost=(0,))],
            [dict(good, requires=())],
            [dict(good, excludes="b")],
            [good, project("a", 2, [0])],
            [project("a", 1, [0], ["a"])],
            [project("a", 1, [0], ["b", "b"]), project("b", 0, [0])],
            [project("a", 1, [0], excludes=["missing"])],
        ]
        for case in bad_projects:
            with self.subTest(projects=case), self.assertRaises(ValueError):
                solve(case, [1])
        for budget in ((1,), [True], [-1], [], [1, 2, 3, 4]):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                solve([good], budget)
        for required in ({"a"}, ["missing"], ["a", "a"]):
            with self.subTest(required=required), self.assertRaises(ValueError):
                solve([good], [1], required)

    def test_arbitrarily_large_integers(self):
        huge = 10 ** 400
        projects = [project("a", huge, [huge], excludes=["b"]),
                    project("b", huge + 1, [huge])]
        self.assertEqual(solve(projects, [huge]),
                         {"selected": ["b"], "value": huge + 1,
                          "cost": [huge]})


if __name__ == "__main__":
    unittest.main()
