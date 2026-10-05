import copy
import itertools
import random
import unittest

from optimizer import solve


def project(name, value, cost, requires=(), excludes=()):
    return dict(id=name, value=value, cost=list(cost),
                requires=list(requires), excludes=list(excludes))


def exhaustive(projects, budget, required=()):
    """Small, independent oracle that checks the definition of feasibility."""
    best_key, best = None, None
    for flags in itertools.product((False, True), repeat=len(projects)):
        chosen = [p for p, flag in zip(projects, flags) if flag]
        names = {p["id"] for p in chosen}
        if not set(required) <= names:
            continue
        if any(not set(p["requires"]) <= names
               or set(p["excludes"]) & names for p in chosen):
            continue
        costs = [sum(p["cost"][d] for p in chosen) for d in range(len(budget))]
        if any(c > b for c, b in zip(costs, budget)):
            continue
        value, ordered = sum(p["value"] for p in chosen), sorted(names)
        key = (-value, tuple(costs), tuple(ordered))
        if best_key is None or key < best_key:
            best_key = key
            best = dict(selected=ordered, value=value, cost=costs)
    return best


class Contract(unittest.TestCase):
    def test_random_against_exhaustive(self):
        rng = random.Random(17309)
        for case in range(240):
            n, dimensions = rng.randrange(0, 10), rng.randrange(1, 4)
            names = ["item_%02d" % i for i in range(n)]
            projects = []
            for i, name in enumerate(names):
                # References to earlier nodes give a DAG, including shared
                # dependencies. Exclusions are deliberately asymmetric.
                requires = [names[j] for j in range(i) if rng.random() < .2]
                excludes = [other for other in names
                            if other != name and rng.random() < .12]
                projects.append(project(name, rng.randrange(-7, 10),
                                        [rng.randrange(0, 5) for _ in range(dimensions)],
                                        requires, excludes))
            rng.shuffle(projects)
            budget = [rng.randrange(0, 12) for _ in range(dimensions)]
            required = [name for name in names if rng.random() < .15]
            with self.subTest(case=case):
                self.assertEqual(solve(projects, budget, required),
                                 exhaustive(projects, budget, required))

    def test_random_independent_and_zero_value_ties(self):
        rng = random.Random(8192)
        for case in range(80):
            dimensions = rng.randrange(1, 4)
            projects = [project("p%02d" % i, rng.randrange(-2, 6),
                                [rng.randrange(0, 4) for _ in range(dimensions)])
                        for i in range(10)]
            # Free optional IDs can precede, interleave, or follow selected IDs.
            projects.extend(project(name, 0, [0] * dimensions)
                            for name in ("a", "p04a", "z"))
            budget = [rng.randrange(2, 10) for _ in range(dimensions)]
            required = ["p09"] if case % 3 == 0 else []
            with self.subTest(case=case):
                self.assertEqual(solve(projects, budget, required),
                                 exhaustive(projects, budget, required))

    def test_disconnected_components_against_exhaustive(self):
        rng = random.Random(66179)
        for case in range(16):
            dimensions = case % 3 + 1
            projects = [project("m_fixed", -2, [0] * dimensions)]
            for i in range(7):
                a, b = "a%02d" % i, "z%02d" % i
                cost_a = [rng.randrange(1, 5) for _ in range(dimensions)]
                cost_b = [rng.randrange(1, 5) for _ in range(dimensions)]
                if i % 3 == 0:
                    projects.extend([project(a, 11, cost_a, [b]),
                                     project(b, -3 if case % 2 == 0 else 2, cost_b)])
                elif i % 3 == 1:
                    projects.extend([project(a, rng.randrange(3, 10), cost_a, excludes=[b]),
                                     project(b, rng.randrange(3, 10), cost_b)])
                else:
                    # Also exercise positive free dependents in the cases
                    # where every optional project has a positive value.
                    projects.extend([project(a, 7, [0] * dimensions if case % 2 else cost_a, [b]),
                                     project(b, 2, cost_b)])
            budget = [rng.randrange(8, 18) for _ in range(dimensions)]
            required = ["m_fixed"]
            with self.subTest(case=case):
                self.assertEqual(solve(projects, budget, required),
                                 exhaustive(projects, budget, required))

    def test_cost_dimension_order_and_id_prefixes(self):
        self.assertEqual(solve([project("a", 4, [2, 0]),
                                project("z", 4, [1, 3])], [2, 3]),
                         dict(selected=["z"], value=4, cost=[1, 3]))
        projects = [project("a", 0, [0]), project("m", 3, [1]),
                    project("z", 0, [0])]
        self.assertEqual(solve(projects, [1]),
                         dict(selected=["a", "m"], value=3, cost=[1]))
        self.assertEqual(solve([project("a", 0, [0]), project("z", 0, [0])], [0]),
                         dict(selected=[], value=0, cost=[0]))
        self.assertEqual(solve(projects, [0], ["z"]),
                         dict(selected=["a", "z"], value=0, cost=[0]))

    def test_negative_and_zero_dependency_bundles(self):
        projects = [project("a", -3, [0], ["b"]),
                    project("b", 3, [0]), project("z", 4, [0])]
        self.assertEqual(solve(projects, [0]),
                         dict(selected=["b", "z"], value=7, cost=[0]))
        self.assertEqual(solve(projects, [0], ["a"]),
                         dict(selected=["a", "b", "z"], value=4, cost=[0]))
        projects = [project("a", 3, [0], ["b"]),
                    project("b", -3, [0]), project("z", 2, [0])]
        self.assertEqual(solve(projects, [0]),
                         dict(selected=["a", "b", "z"], value=2, cost=[0]))

    def test_closure_conflicts_and_infeasible_required(self):
        projects = [project("a", 20, [0], ["b"], ["b"]),
                    project("b", 2, [0]), project("c", -1, [0])]
        self.assertEqual(solve(projects, [0]),
                         dict(selected=["b"], value=2, cost=[0]))
        self.assertIsNone(solve(projects, [0], ["a"]))
        self.assertEqual(solve(projects, [0], ["c"]),
                         dict(selected=["b", "c"], value=1, cost=[0]))
        self.assertIsNone(solve([project("a", -1, [2])], [1], ["a"]))

    def test_large_integers_and_python_string_order(self):
        huge = 10 ** 400
        projects = [project("é", huge + 1, [huge, 1]),
                    project("Z", huge, [huge - 1, 1]),
                    project("a", 2, [1, 0]), project("A", 0, [0, 0])]
        self.assertEqual(solve(projects, [huge, 1]),
                         dict(selected=["A", "Z", "a"], value=huge + 2,
                              cost=[huge, 1]))

    def test_inputs_are_not_mutated(self):
        projects = [project("z", 6, [2, 1], ["a"]),
                    project("a", -1, [1, 0]), project("b", 0, [0, 0], excludes=["z"])]
        budget, required = [4, 3], ["a"]
        before = copy.deepcopy((projects, budget, required))
        solve(projects, budget, required)
        self.assertEqual((projects, budget, required), before)

    def test_validation_containers_and_budget(self):
        for projects in (None, (), {}, "", [project(str(i), 0, [0]) for i in range(33)]):
            with self.subTest(projects=type(projects).__name__):
                with self.assertRaises(ValueError):
                    solve(projects, [1])
        for budget in (None, (), [1, 2, 3, 4], [], [True], [-1], [1.0], ["1"]):
            with self.subTest(budget=budget):
                with self.assertRaises(ValueError):
                    solve([], budget)
        for required in (None, set(), "a", 1, ["unknown"], [True], [["a"]], ["a", "a"]):
            with self.subTest(required=required):
                with self.assertRaises(ValueError):
                    solve([project("a", 0, [0])], [0], required)

    def test_validation_fields(self):
        base = project("a", 0, [0])
        variants = [None, [], {}, {**base, "extra": 1}]
        variants.extend({k: v for k, v in base.items() if k != omitted} for omitted in base)
        for field, malformed in (("id", ("", 4, True, [])),
                                 ("value", (True, False, 1.0, "1", None)),
                                 ("cost", ((), [True], [-1], [1.0], [], [0, 1])),
                                 ("requires", ((), "a", ["a"], ["missing"], [4], [[]])),
                                 ("excludes", ((), "a", ["a"], ["missing"], [False], [[]]))):
            variants.extend({**base, field: value} for value in malformed)
        for p in variants:
            with self.subTest(project=p):
                with self.assertRaises(ValueError):
                    solve([p], [0])
        with self.assertRaises(ValueError):
            solve([base, copy.deepcopy(base)], [0])
        for field in ("requires", "excludes"):
            with self.assertRaises(ValueError):
                solve([{**base, field: ["b", "b"]}, project("b", 1, [0])], [0])

    def test_validate_even_unaffordable_or_infeasible(self):
        with self.assertRaises(ValueError):
            solve([project("a", 1, [100], ["b"]),
                   project("b", 1, [100], ["a"])], [0], ["a"])
        with self.assertRaises(ValueError):
            solve([project("a", 1, [100]), project("b", True, [100])], [0], ["a"])
        with self.assertRaises(ValueError):
            solve([project("a", 1, [100], excludes=["unknown"])], [0])

    def test_32_shared_negative_prerequisite(self):
        projects = [project("zroot", -100, [1])]
        projects.extend(project("a%02d" % i, 8, [1], ["zroot"]) for i in range(31))
        self.assertEqual(solve(projects, [20]),
                         dict(selected=["a%02d" % i for i in range(19)] + ["zroot"],
                              value=52, cost=[20]))

    def test_32_independent_in_each_dimension(self):
        # Each value exceeds the sum of all smaller values, so the exact
        # answer follows by considering items in decreasing value order.
        # Large varied costs keep the two half-enumerations from collapsing
        # to just a few identical cost states, exercising all range joins.
        rng = random.Random(46109)
        for dimensions in (1, 2, 3):
            projects = [project("p%02d" % i, 1 << i,
                                [rng.randrange(100000, 1000000)
                                 for _ in range(dimensions)]) for i in range(32)]
            budget = [sum(p["cost"][d] for p in projects) // 2
                      for d in range(dimensions)]
            room, chosen = list(budget), []
            for p in reversed(projects):
                if all(c <= b for c, b in zip(p["cost"], room)):
                    chosen.append(p)
                    room = [b - c for b, c in zip(room, p["cost"])]
            with self.subTest(dimensions=dimensions):
                self.assertEqual(solve(projects, budget),
                                 dict(selected=sorted(p["id"] for p in chosen),
                                      value=sum(p["value"] for p in chosen),
                                      cost=[b - r for b, r in zip(budget, room)]))

    def test_32_negative_dependency_pairs(self):
        projects = []
        for i in range(16):
            a, b = "a%02d" % i, "b%02d" % i
            projects.extend([project(a, 101, [1], [b]), project(b, -100, [1])])
        self.assertEqual(solve(projects, [16]),
                         dict(selected=["a%02d" % i for i in range(8)]
                              + ["b%02d" % i for i in range(8)], value=8, cost=[16]))

    def test_32_exclusion_pairs_and_dependency_chain(self):
        projects = []
        for i in range(16):
            a, b = "p%02da" % i, "p%02db" % i
            projects.extend([project(a, 1, [1], excludes=[b]), project(b, 1, [1])])
        self.assertEqual(solve(projects, [16]),
                         dict(selected=["p%02da" % i for i in range(16)], value=16, cost=[16]))
        projects = [project("p%02d" % i, 2, [1], ["p%02d" % (i - 1)] if i else [])
                    for i in range(32)]
        self.assertEqual(solve(projects, [17]),
                         dict(selected=["p%02d" % i for i in range(17)], value=34, cost=[17]))


if __name__ == "__main__":
    unittest.main()
