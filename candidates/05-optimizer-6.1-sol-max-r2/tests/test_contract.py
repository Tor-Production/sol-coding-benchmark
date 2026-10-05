import copy
import random
import unittest
from unittest.mock import patch

from optimizer import solve


def project(name, value=1, cost=(0,), requires=(), excludes=()):
    return {"id": name, "value": value, "cost": list(cost),
            "requires": list(requires), "excludes": list(excludes)}


def exhaustive(projects, budget, required=()):
    """Independent small-instance oracle using the contract's tuple ordering."""
    best = None
    answer = None
    for mask in range(1 << len(projects)):
        chosen = [p for i, p in enumerate(projects) if mask & (1 << i)]
        names = {p["id"] for p in chosen}
        if not set(required) <= names:
            continue
        if any(not set(p["requires"]) <= names or set(p["excludes"]) & names
               for p in chosen):
            continue
        cost = tuple(sum(p["cost"][j] for p in chosen) for j in range(len(budget)))
        if any(c > cap for c, cap in zip(cost, budget)):
            continue
        value = sum(p["value"] for p in chosen)
        ids = tuple(sorted(names))
        key = (-value, cost, ids)
        if best is None or key < best:
            best = key
            answer = {"selected": list(ids), "value": value, "cost": list(cost)}
    return answer


class Contract(unittest.TestCase):
    def test_random_instances_against_exhaustive_oracle(self):
        rng = random.Random(39107)
        alphabet = ["a", "a0", "b", "d", "q", "z", "zz", "é", "Ω", "中"]
        for case in range(250):
            n = rng.randrange(11)
            dimensions = rng.randrange(1, 4)
            names = rng.sample(alphabet, n)
            projects = []
            for i, name in enumerate(names):
                requires = [names[j] for j in range(i) if rng.random() < 0.16]
                excludes = [names[j] for j in range(n)
                            if j != i and rng.random() < 0.12]
                projects.append(project(name, rng.randrange(-5, 11),
                                        [rng.randrange(5) for _ in range(dimensions)],
                                        requires, excludes))
            rng.shuffle(projects)
            budget = [rng.randrange(15) for _ in range(dimensions)]
            required = rng.sample(names, rng.randrange(min(n, 3) + 1))
            before = copy.deepcopy((projects, budget, required))
            with self.subTest(case=case):
                self.assertEqual(solve(projects, budget, required),
                                 exhaustive(projects, budget, required))
                self.assertEqual((projects, budget, required), before)

    def test_branch_search_against_exhaustive_oracle(self):
        rng = random.Random(74623)
        # Small limits force both component and table fallback paths, so the
        # branch bounds are tested against complete enumeration as well.
        with patch("optimizer._Optimizer.ENUMERATION_LIMIT", 4), \
                patch("optimizer._Optimizer.TABLE_LIMIT", 4):
            for case in range(100):
                n = rng.randrange(3, 10)
                dimensions = rng.randrange(1, 4)
                projects = []
                for i in range(n):
                    projects.append(project(
                        "p%d" % i, rng.randrange(-3, 9),
                        [rng.randrange(4) for _ in range(dimensions)],
                        ["p%d" % j for j in range(i) if rng.random() < 0.12],
                        ["p%d" % j for j in range(i) if rng.random() < 0.12]))
                budget = [rng.randrange(1, 12) for _ in range(dimensions)]
                required = ["p%d" % rng.randrange(n)] if case % 4 == 0 else []
                with self.subTest(case=case):
                    self.assertEqual(solve(projects, budget, required),
                                     exhaustive(projects, budget, required))

    def test_prefix_and_interleaved_ties(self):
        cases = [
            ([project("a", 0), project("m", 4), project("z", 0)], (), ["a", "m"]),
            ([project("a", 0), project("z", 0)], (), []),
            ([project("a", 0), project("m", 0), project("z", 0)], ("m",), ["a", "m"]),
            ([project("a", 0), project("b", 1, requires=["c"]),
              project("c", -1)], ("a",), ["a"]),
            ([project("a", 0, excludes=["c"]), project("b", 0),
              project("c", 0), project("z", 1)], (), ["a", "b", "z"]),
        ]
        for projects, required, expected in cases:
            with self.subTest(expected=expected):
                answer = solve(projects, [0], required)
                self.assertEqual(answer["selected"], expected)
                self.assertEqual(answer, exhaustive(projects, [0], required))

    def test_cost_dimension_order(self):
        projects = [project("a", 7, [2, 0, 1], excludes=["z"]),
                    project("z", 7, [1, 9, 1])]
        self.assertEqual(solve(projects, [2, 9, 1]),
                         {"selected": ["z"], "value": 7, "cost": [1, 9, 1]})

    def test_unbounded_integer_arithmetic(self):
        value, cost = 10 ** 100, 10 ** 150
        projects = [project("a", value, [cost, 0], excludes=["b"]),
                    project("b", value + 1, [cost, cost]),
                    project("c", -value, [0, 0])]
        self.assertEqual(solve(projects, [cost, cost]),
                         {"selected": ["b"], "value": value + 1, "cost": [cost, cost]})
        self.assertEqual(solve(projects, [cost, cost], ["c"]),
                         {"selected": ["b", "c"], "value": 1, "cost": [cost, cost]})

    def test_asymmetric_exclusions_and_infeasible_closures(self):
        projects = [project("a", 9, [1], requires=["b"], excludes=["b"]),
                    project("b", 2, [1])]
        self.assertEqual(solve(projects, [5]),
                         {"selected": ["b"], "value": 2, "cost": [1]})
        self.assertIsNone(solve(projects, [5], ["a"]))
        projects = [project("a", 9, [1], requires=["b"]),
                    project("b", -1, [1], excludes=["c"]),
                    project("c", 5, [1])]
        self.assertIsNone(solve(projects, [5], ["a", "c"]))

    def test_validation(self):
        bad_calls = [
            ((), [1], ()),
            ([project(str(i)) for i in range(33)], [1], ()),
            ([], (), ()), ([], [], ()), ([], [0, 0, 0, 0], ()),
            ([], [True], ()), ([], [-1], ()), ([], [1.0], ()),
            ([], [0], ""), ([], [0], None), ([], [0], ["unknown"]),
            ([project("a"), project("a")], [0], ()),
            ([project("a")], [0], ["a", "a"]),
            ([project("a")], [0], [True]),
            ([project("a"), None], [0], ()),
        ]
        bad_fields = {
            "id": ["", 1, True, None],
            "value": [True, False, 1.5, "1", None],
            "cost": [(0,), [], [0, 0], [True], [-1], [1.5], None],
            "requires": [(), None, ["a"], ["missing"], [True], [""], ["b", "b"]],
            "excludes": [(), None, ["a"], ["missing"], [True], [""], ["b", "b"]],
        }
        for field, invalid_values in bad_fields.items():
            for value in invalid_values:
                item = project("a")
                item[field] = value
                bad_calls.append(([item, project("b")], [0], ()))
        for field in project("a"):
            item = project("a")
            del item[field]
            bad_calls.append(([item], [0], ()))
        extra = project("a")
        extra["extra"] = 1
        bad_calls.append(([extra], [0], ()))
        # Invalid references and cycles must be caught even in over-budget projects.
        bad_calls.extend([
            ([project("a", cost=[100], requires=["b"]),
              project("b", requires=["a"])], [0], ()),
            ([project("a", cost=[100], requires=["unknown"])], [0], ()),
        ])
        for args in bad_calls:
            with self.subTest(args=args), self.assertRaises(ValueError):
                solve(*args)

    def test_32_project_path(self):
        projects = [project("p%02d" % i, 1, [1],
                            excludes=["p%02d" % (i + 1)] if i < 31 else [])
                    for i in range(32)]
        self.assertEqual(solve(projects, [16]),
                         {"selected": ["p%02d" % i for i in range(0, 32, 2)],
                          "value": 16, "cost": [16]})

    def test_32_project_chain_and_dependency_star(self):
        projects = [project("p%02d" % i, 1, [1],
                            requires=["p%02d" % (i - 1)] if i else [])
                    for i in range(32)]
        self.assertEqual(solve(projects, [20]),
                         {"selected": ["p%02d" % i for i in range(20)],
                          "value": 20, "cost": [20]})
        projects = [project("hub", -5, [1])]
        projects.extend(project("p%02d" % i, i + 1, [1], requires=["hub"])
                        for i in range(31))
        self.assertEqual(solve(projects, [7]),
                         {"selected": ["hub"] + ["p%02d" % i for i in range(25, 31)],
                          "value": sum(range(26, 32)) - 5, "cost": [7]})

    def test_32_independent_binary_costs_three_dimensions(self):
        # Every integer up to 2**32-1 has exactly one feasible binary expansion.
        target = 0xABCDEF01
        projects = [project("p%02d" % i, 1 << i, [1 << i] * 3) for i in range(32)]
        self.assertEqual(solve(projects, [target] * 3),
                         {"selected": ["p%02d" % i for i in range(32) if target & (1 << i)],
                          "value": target, "cost": [target] * 3})


if __name__ == "__main__":
    unittest.main()
