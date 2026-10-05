import copy
import unittest

from optimizer import solve


def project(name, value, cost, requires=(), excludes=()):
    return {
        "id": name,
        "value": value,
        "cost": list(cost),
        "requires": list(requires),
        "excludes": list(excludes),
    }


class ContractTests(unittest.TestCase):
    def test_transitive_dependency_and_one_sided_exclusion(self):
        projects = [
            project("top", 10, [1], ["middle"]),
            project("middle", -2, [1], ["base"]),
            project("base", -1, [1]),
            project("other", 6, [1], excludes=["base"]),
        ]
        self.assertEqual(
            solve(projects, [3]),
            {"selected": ["base", "middle", "top"], "value": 7, "cost": [3]},
        )
        self.assertIsNone(solve(projects, [4], ["top", "other"]))

    def test_cost_dimensions_are_lexicographic(self):
        projects = [
            project("a", 5, [1, 0], excludes=["z"]),
            project("z", 5, [0, 9]),
        ]
        self.assertEqual(solve(projects, [1, 9])["selected"], ["z"])

    def test_id_tuple_tie_can_add_or_omit_zero_project(self):
        projects = [
            project("a", 0, [0]),
            project("z", 4, [1]),
            project("zz", 0, [0]),
        ]
        self.assertEqual(solve(projects, [1], ["z"])["selected"], ["a", "z"])

    def test_requires_and_excludes_same_id_is_valid_but_infeasible(self):
        projects = [project("a", 3, [0], ["b"], ["b"]), project("b", 1, [0])]
        self.assertEqual(solve(projects, [0]), {"selected": ["b"], "value": 1, "cost": [0]})
        self.assertIsNone(solve(projects, [0], ["a"]))

    def test_inputs_are_not_mutated(self):
        projects = [project("a", 2, [1, 0]), project("b", 1, [0, 1], ["a"])]
        budget = [1, 1]
        required = ["b"]
        before = copy.deepcopy((projects, budget, required))
        solve(projects, budget, required)
        self.assertEqual((projects, budget, required), before)

    def test_cycles_and_malformed_fields_raise(self):
        with self.assertRaises(ValueError):
            solve([project("a", 1, [0], ["b"]), project("b", 1, [0], ["a"])], [0])
        with self.assertRaises(ValueError):
            solve([project("a", 1, [False])], [0])
        malformed = project("a", 1, [0])
        malformed["extra"] = 1
        with self.assertRaises(ValueError):
            solve([malformed], [0])

    def test_structured_32_project_dependency_bundles(self):
        projects = []
        for i in range(16):
            projects.append(project(f"r{i:02}", -1, [0]))
            projects.append(project(f"d{i:02}", i + 2, [1], [f"r{i:02}"]))
        result = solve(projects, [8])
        expected = sorted(
            [name for i in range(8, 16) for name in (f"d{i:02}", f"r{i:02}")]
        )
        self.assertEqual(result, {"selected": expected, "value": 100, "cost": [8]})


if __name__ == "__main__":
    unittest.main()
