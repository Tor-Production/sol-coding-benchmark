import copy
import itertools
import random
import unittest

from optimizer import solve


def project(name, value=0, cost=(0,), requires=(), excludes=()):
    return {"id": name, "value": value, "cost": list(cost),
            "requires": list(requires), "excludes": list(excludes)}


def exhaustive(projects, budget, required=()):
    """Small independent oracle, using direct constraints and Python tuple order."""
    best = None
    for flags in itertools.product((False, True), repeat=len(projects)):
        selected = [p for p, take in zip(projects, flags) if take]
        names = {p["id"] for p in selected}
        if not set(required) <= names:
            continue
        if any(not set(p["requires"]) <= names or set(p["excludes"]) & names
               for p in selected):
            continue
        cost = tuple(sum(p["cost"][d] for p in selected) for d in range(len(budget)))
        if any(c > b for c, b in zip(cost, budget)):
            continue
        value = sum(p["value"] for p in selected)
        key = (-value, cost, tuple(sorted(names)))
        if best is None or key < best:
            best = key
    if best is None:
        return None
    return {"selected": list(best[2]), "value": -best[0], "cost": list(best[1])}


class Contract(unittest.TestCase):
    def test_random_small_instances_against_exhaustive_search(self):
        rng = random.Random(314159)
        for case in range(350):
            n, dimensions = rng.randrange(10), rng.randrange(1, 4)
            names = ["", "a", "aa", "b", "z", "é", "Ω", "🙂", "0", "_"][1:n + 1]
            rng.shuffle(names)
            projects = []
            for i, name in enumerate(names):
                projects.append(project(
                    name, rng.randrange(-6, 12),
                    [rng.randrange(5) for _ in range(dimensions)],
                    [x for x in names[:i] if rng.random() < .18],
                    [x for x in names if x != name and rng.random() < .1]))
            budget = [rng.randrange(13) for _ in range(dimensions)]
            required = rng.sample(names, rng.randrange(min(n, 3) + 1))
            rng.shuffle(projects)
            before = copy.deepcopy((projects, budget, required))
            with self.subTest(case=case):
                self.assertEqual(solve(projects, budget, required),
                                 exhaustive(projects, budget, required))
                self.assertEqual((projects, budget, required), before)

    def test_tie_heavy_instances_against_exhaustive_search(self):
        rng = random.Random(271828)
        for case in range(60):
            names = [f"p{i:02}" for i in range(11)]
            rng.shuffle(names)
            projects = [project(name, rng.randrange(-1, 2), [rng.randrange(2), rng.randrange(2)],
                                [x for x in names[:i] if rng.random() < .12],
                                [x for x in names if x != name and rng.random() < .08])
                        for i, name in enumerate(names)]
            required = rng.sample(names, rng.randrange(3))
            budget = [rng.randrange(6), rng.randrange(6)]
            with self.subTest(case=case):
                self.assertEqual(solve(projects, budget, required),
                                 exhaustive(projects, budget, required))

    def test_cost_dimension_order(self):
        projects = [project("a", 5, [0, 4], excludes=["b"]),
                    project("b", 5, [1, 0])]
        self.assertEqual(solve(projects, [1, 4]),
                         {"selected": ["a"], "value": 5, "cost": [0, 4]})

    def test_zero_value_lexical_prefixes(self):
        projects = [project(x) for x in ("a", "aa", "b", "c", "z")]
        self.assertEqual(solve(projects, [0])["selected"], [])
        self.assertEqual(solve(projects, [0], ["b"])["selected"], ["a", "aa", "b"])
        projects[-1]["value"] = 1
        self.assertEqual(solve(projects, [0])["selected"], ["a", "aa", "b", "c", "z"])

    def test_dependencies_conflicts_and_negative_values(self):
        projects = [project("a", 10, [1], ["b"]),
                    project("b", -3, [1], ["c"]),
                    project("c", -2, [1]), project("d", 6, [2], excludes=["c"])]
        for required in ((), ["a"], ["a", "d"], ["c"], ["d"]):
            self.assertEqual(solve(projects, [4], required), exhaustive(projects, [4], required))
        impossible = [project("a", 100, [0], ["b"], ["b"]), project("b")]
        self.assertEqual(solve(impossible, [0])["selected"], [])
        self.assertIsNone(solve(impossible, [0], ["a"]))

    def test_arbitrarily_large_integers(self):
        huge = 10 ** 400
        projects = [project("a", huge, [huge, 0]), project("b", huge + 1, [0, huge])]
        self.assertEqual(solve(projects, [huge, huge]),
                         {"selected": ["a", "b"], "value": 2 * huge + 1,
                          "cost": [huge, huge]})

    def test_validation(self):
        bad_projects = [None, (), {}, [None], [project("")], [project(1)],
                        [project("a"), project("a")], [project("a")] * 33]
        for projects in bad_projects:
            with self.subTest(projects=projects), self.assertRaises(ValueError):
                solve(projects, [1])
        for budget in (None, (), {}, [], [1] * 4, [True], [-1], [1.0], ["1"]):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                solve([], budget)
        changes = {
            "value": [True, False, 1.0, "1", None],
            "cost": [(), None, {}, [], [0, 0], [-1], [True], [1.0]],
            "requires": [(), None, {}, "b", ["a"], ["unknown"], ["b", "b"], [1], [[]]],
            "excludes": [(), None, {}, "b", ["a"], ["unknown"], ["b", "b"], [1], [[]]],
        }
        for field, malformed in changes.items():
            for value in malformed:
                a = project("a", cost=[100])
                a[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    solve([a, project("b")], [0])
        for field in project("a"):
            malformed = project("a")
            del malformed[field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                solve([malformed], [0])
        extra = project("a")
        extra["extra"] = 1
        with self.assertRaises(ValueError):
            solve([extra], [0])
        for required in (None, "a", {"a"}, ["unknown"], ["a", "a"], [True], [[]]):
            with self.subTest(required=required), self.assertRaises(ValueError):
                solve([project("a")], [0], required)
        # Neither budget impossibility nor an infeasible required set hides a cycle.
        with self.assertRaises(ValueError):
            solve([project("a", cost=[100], requires=["b"]),
                   project("b", requires=["c"]), project("c", requires=["a"])], [0], ["a"])


class StructuredScale(unittest.TestCase):
    def test_32_independent_projects_against_cost_dynamic_programming(self):
        # An independent oracle with small integer budgets verifies all three
        # range-query paths on heterogeneous costs, rather than only uniform costs.
        rng = random.Random(161803)
        for dimensions in (1, 2, 3):
            projects = [project(f"p{i:02}", rng.randrange(1, 20),
                                [rng.randrange(1, 5) for _ in range(dimensions)])
                        for i in range(32)]
            budget = [12] * dimensions
            states = {(0,) * dimensions: (0, ())}
            for p in projects:
                for old_cost, (old_value, old_ids) in list(states.items()):
                    cost = tuple(c + d for c, d in zip(old_cost, p["cost"]))
                    if any(c > b for c, b in zip(cost, budget)):
                        continue
                    candidate = (old_value + p["value"], old_ids + (p["id"],))
                    old = states.get(cost)
                    if old is None or (-candidate[0], candidate[1]) < (-old[0], old[1]):
                        states[cost] = candidate
            value, cost, selected = min((-value, cost, names)
                                       for cost, (value, names) in states.items())
            with self.subTest(dimensions=dimensions):
                self.assertEqual(solve(projects, budget),
                                 {"selected": list(selected), "value": -value, "cost": list(cost)})

    def test_32_independent_projects(self):
        projects = [project(f"p{i:02}", i + 1, [1, 2, 3]) for i in range(32)]
        self.assertEqual(solve(projects, [16, 32, 48]),
                         {"selected": [f"p{i:02}" for i in range(16, 32)],
                          "value": sum(range(17, 33)), "cost": [16, 32, 48]})

    def test_16_exclusion_pairs_with_interleaved_halves(self):
        projects = [project(f"a{i:02}", 1, [1], excludes=[f"z{i:02}"]) for i in range(16)]
        projects += [project(f"z{i:02}", 1, [1]) for i in range(16)]
        self.assertEqual(solve(projects, [16]),
                         {"selected": [f"a{i:02}" for i in range(16)], "value": 16, "cost": [16]})

    def test_chain_and_shared_dependency(self):
        chain = [project(f"p{i:02}", 1, [1], [f"p{i + 1:02}"] if i < 31 else [])
                 for i in range(32)]
        self.assertEqual(solve(chain, [16]),
                         {"selected": [f"p{i:02}" for i in range(16, 32)], "value": 16, "cost": [16]})
        shared = [project(f"p{i:02}", 1, [1], ["z"]) for i in range(31)]
        shared += [project("z", -10, [3])]
        self.assertEqual(solve(shared, [19]),
                         {"selected": [f"p{i:02}" for i in range(16)] + ["z"],
                          "value": 6, "cost": [19]})

    def test_32_zero_cost_ties(self):
        projects = [project(f"p{i:02}") for i in range(32)]
        self.assertEqual(solve(projects, [0]), {"selected": [], "value": 0, "cost": [0]})
        self.assertEqual(solve(projects, [0], ["p15"]),
                         {"selected": [f"p{i:02}" for i in range(16)], "value": 0, "cost": [0]})


if __name__ == "__main__":
    unittest.main()
