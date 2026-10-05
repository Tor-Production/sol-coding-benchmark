import copy
import random
import unittest

from optimizer import solve


def project(name, value=0, cost=(0,), requires=(), excludes=()):
    return {"id": name, "value": value, "cost": list(cost),
            "requires": list(requires), "excludes": list(excludes)}


def exhaustive(projects, budget, required=()):
    """Small independent oracle using the contract directly."""
    best = None
    answer = None
    for mask in range(1 << len(projects)):
        selected = [p for i, p in enumerate(projects) if mask & (1 << i)]
        names = {p["id"] for p in selected}
        if not set(required) <= names:
            continue
        if any(not set(p["requires"]) <= names
               or set(p["excludes"]) & names for p in selected):
            continue
        cost = tuple(sum(p["cost"][d] for p in selected)
                     for d in range(len(budget)))
        if any(c > b for c, b in zip(cost, budget)):
            continue
        value = sum(p["value"] for p in selected)
        key = (-value, cost, tuple(sorted(names)))
        if best is None or key < best:
            best = key
            answer = {"selected": list(key[2]), "value": value,
                      "cost": list(cost)}
    return answer


class OptimizerContract(unittest.TestCase):
    def test_multidimensional_budget_indexes_against_oracle(self):
        rng = random.Random(9057)
        projects = [project("p%02d" % i, rng.randrange(-2, 16),
                            [rng.randrange(1, 9) for _ in range(3)])
                    for i in range(16)]
        # Every subset within either half fits: each right cost index therefore
        # spans multiple prefix blocks, while combined portfolios need filtering.
        budget = [max(sum(p["cost"][d] for p in projects[:8]),
                      sum(p["cost"][d] for p in projects[8:]))
                  for d in range(3)]
        self.assertEqual(solve(projects, budget), exhaustive(projects, budget))

    def test_random_instances_against_exhaustive_oracle(self):
        rng = random.Random(81473)
        for trial in range(350):
            n = rng.randrange(11)
            dimensions = rng.randrange(1, 4)
            ids = ["id%02d" % i for i in range(n)]
            rng.shuffle(ids)  # Dependency order differs from string order.
            projects = []
            for i, name in enumerate(ids):
                requires = [ref for ref in ids[:i] if rng.random() < 0.18]
                excludes = [ref for ref in ids if ref != name
                            and rng.random() < 0.10]
                projects.append(project(name, rng.randrange(-8, 13),
                                        [rng.randrange(7) for _ in range(dimensions)],
                                        requires, excludes))
            rng.shuffle(projects)
            budget = [rng.randrange(16) for _ in range(dimensions)]
            required = [name for name in ids if rng.random() < 0.12]
            with self.subTest(trial=trial):
                self.assertEqual(solve(projects, budget, required),
                                 exhaustive(projects, budget, required))

    def test_id_tuple_prefix_ties(self):
        cases = [
            ([project("a"), project("b", 1), project("c")], [], ["a", "b"]),
            ([project("a"), project("b"), project("c")], [], []),
            ([project("a"), project("b"), project("c")], ["c"], ["a", "b", "c"]),
            ([project("a"), project("b"), project("c")], ["b"], ["a", "b"]),
        ]
        for projects, required, expected in cases:
            self.assertEqual(solve(projects, [0], required)["selected"], expected)

    def test_cost_dimensions_have_lexicographic_order(self):
        projects = [project("a", 5, [1, 0, 0], excludes=["z"]),
                    project("z", 5, [0, 7, 9])]
        self.assertEqual(solve(projects, [1, 7, 9]),
                         {"selected": ["z"], "value": 5, "cost": [0, 7, 9]})

    def test_transitive_conflicts_and_negative_dependencies(self):
        projects = [project("a", 12, [1], ["z"]),
                    project("z", -4, [2], ["m"]),
                    project("m", 0, [0]),
                    project("b", 9, [3], excludes=["m"])]
        self.assertEqual(solve(projects, [3])["selected"], ["b"])
        self.assertEqual(solve(projects, [3], ["a"]),
                         {"selected": ["a", "m", "z"], "value": 8, "cost": [3]})
        self.assertIsNone(solve(projects, [3], ["a", "b"]))
        contradictory = [project("a", 10, [0], ["b"], ["b"]), project("b")]
        self.assertEqual(solve(contradictory, [0])["selected"], [])
        self.assertIsNone(solve(contradictory, [0], ["a"]))

    def test_input_is_not_mutated_and_large_integers(self):
        huge = 10 ** 100
        projects = [project("z", huge, [huge, 0]),
                    project("a", huge + 1, [0, huge], ["b"]),
                    project("b", -1, [0, 0])]
        budget = [huge, huge]
        required = ["a"]
        original = copy.deepcopy((projects, budget, required))
        self.assertEqual(solve(projects, budget, required),
                         exhaustive(projects, budget, required))
        self.assertEqual((projects, budget, required), original)

    def test_invalid_inputs(self):
        good = [project("a"), project("b")]
        cases = [
            ((), [1], ()), ([None], [1], ()), (good * 17, [1], ()),
            (good, (), ()), (good, [], ()), (good, [0] * 4, ()),
            (good, [True], ()), (good, [-1], ()), (good, [1.0], ()),
            (good, [1], "a"), (good, [1], {"a"}),
            (good, [1], ["missing"]), (good, [1], ["a", "a"]),
            (good, [1], [[]]), (good, [1], [True]),
            ([project("a"), project("a")], [1], ()),
        ]
        for field, bad_values in {
            "id": ["", 1, None, []],
            "value": [True, False, 1.5, "3", None],
            "cost": [(), [True], [-1], [1.5], [], [0, 0]],
            "requires": [(), "b", ["a"], ["missing"], ["b", "b"], [1], [[]]],
            "excludes": [(), "b", ["a"], ["missing"], ["b", "b"], [1], [[]]],
        }.items():
            for bad in bad_values:
                projects = copy.deepcopy(good)
                projects[0][field] = bad
                cases.append((projects, [1], ()))
        missing = project("a")
        del missing["excludes"]
        extra = project("a")
        extra["extra"] = 0
        cases.extend([([missing], [1], ()), ([extra], [1], ())])
        for projects, budget, required in cases:
            with self.subTest(projects=projects, budget=budget, required=required):
                with self.assertRaises(ValueError):
                    solve(projects, budget, required)

    def test_validation_before_infeasibility(self):
        invalid_cases = [
            [project("a", cost=[100], requires=["b"]),
             project("b", requires=["a"])],
            [project("a", cost=[100]), project("b", value=True)],
            [project("a", cost=[100], excludes=["unknown"])],
        ]
        for projects in invalid_cases:
            with self.assertRaises(ValueError):
                solve(projects, [0], ["a"])

    def test_32_independent_capacity_and_ties(self):
        projects = [project("p%02d" % i, 1, [1, 1, 1]) for i in range(32)]
        self.assertEqual(solve(projects, [16, 16, 16]),
                         {"selected": ["p%02d" % i for i in range(16)],
                          "value": 16, "cost": [16, 16, 16]})

    def test_32_cross_half_dependency_pairs(self):
        projects = [project("a%02d" % i, 10, [1], ["z%02d" % i])
                    for i in range(16)]
        projects += [project("z%02d" % i, -1, [1]) for i in range(16)]
        expected = ["a%02d" % i for i in range(8)]
        expected += ["z%02d" % i for i in range(8)]
        self.assertEqual(solve(projects, [16]),
                         {"selected": expected, "value": 72, "cost": [16]})

    def test_32_zero_cost_prefix_tie(self):
        projects = [project("p%02d" % i) for i in range(32)]
        self.assertEqual(solve(projects, [0], ["p31"]),
                         {"selected": ["p%02d" % i for i in range(32)],
                          "value": 0, "cost": [0]})

    def test_32_chain(self):
        projects = [project("p%02d" % i, 1, [1],
                            ["p%02d" % (i - 1)] if i else [])
                    for i in range(32)]
        self.assertEqual(solve(projects, [17]),
                         {"selected": ["p%02d" % i for i in range(17)],
                          "value": 17, "cost": [17]})
        self.assertIsNone(solve(projects, [17], ["p31"]))

    def test_32_correlated_values_and_costs(self):
        projects = [project("p%02d" % i, i + 1, [i + 1] * 3)
                    for i in range(32)]
        # The budget bounds value by 264. Taking weights 1..20, 22, and 32
        # attains it. Weight 21 would leave 33, which cannot be made from
        # remaining weights 22..32, establishing the ID tie breaker as well.
        selected = ["p%02d" % i for i in range(20)] + ["p21", "p31"]
        self.assertEqual(solve(projects, [264] * 3),
                         {"selected": selected, "value": 264, "cost": [264] * 3})


if __name__ == "__main__":
    unittest.main()
