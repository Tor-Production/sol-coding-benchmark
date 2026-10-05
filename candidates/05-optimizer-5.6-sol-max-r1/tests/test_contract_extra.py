import copy
import unittest

from optimizer import solve


def project(name, value=0, cost=(0,), requires=(), excludes=()):
    return {
        "id": name,
        "value": value,
        "cost": list(cost),
        "requires": list(requires),
        "excludes": list(excludes),
    }


class ContractRegressionTests(unittest.TestCase):
    def test_cost_dimensions_are_lexicographic(self):
        projects = [
            project("later", 7, (0, 5), excludes=("first",)),
            project("first", 7, (1, 0)),
        ]
        self.assertEqual(
            solve(projects, [1, 5]),
            {"selected": ["later"], "value": 7, "cost": [0, 5]},
        )

    def test_selected_id_tuple_not_just_smallest_mask(self):
        projects = [
            project("a", 0, excludes=("b",)),
            project("b", 3, excludes=("z",)),
            project("z", 3, excludes=("b",)),
        ]
        # ("a", "z") is lexicographically before ("b",), even though it
        # contains an extra zero-value project.
        self.assertEqual(solve(projects, [0])["selected"], ["a", "z"])

    def test_tuple_prefix_wins(self):
        projects = [project("a", 2), project("b", 0)]
        self.assertEqual(solve(projects, [0])["selected"], ["a"])

    def test_zero_value_ids_before_a_required_id_are_selected(self):
        projects = [
            project("a"),
            project("b"),
            project("z"),
            project("zz"),
        ]
        self.assertEqual(solve(projects, [0], ["z"])["selected"], ["a", "b", "z"])
        self.assertEqual(solve(projects, [0])["selected"], [])

    def test_dependency_conflict_and_asymmetric_exclusion(self):
        projects = [
            project("a", 10, requires=("b",)),
            project("b", -2),
            project("c", 20, excludes=("b",)),
        ]
        self.assertIsNone(solve(projects, [0], ("a", "c")))
        self.assertEqual(solve(projects, [0], ("a",))["selected"], ["a", "b"])

    def test_input_is_not_mutated(self):
        projects = [project("a", 1, (1,), requires=())]
        budget = [1]
        required = ["a"]
        snapshot = copy.deepcopy((projects, budget, required))
        solve(projects, budget, required)
        self.assertEqual((projects, budget, required), snapshot)

    def test_validation_rejects_cycles_duplicates_and_bool_cost(self):
        with self.assertRaises(ValueError):
            solve(
                [
                    project("a", requires=("b",)),
                    project("b", requires=("a",)),
                ],
                [0],
            )
        with self.assertRaises(ValueError):
            solve([project("a", requires=("b", "b")), project("b")], [0])
        with self.assertRaises(ValueError):
            solve([project("a", cost=(True,))], [1])


if __name__ == "__main__":
    unittest.main()
