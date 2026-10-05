import copy
import itertools
import random
import unittest

from optimizer import solve


def brute_force(projects, budget, required=()):
    by_id = {project["id"]: project for project in projects}
    ids = sorted(by_id)
    best = None
    for choices in itertools.product((False, True), repeat=len(ids)):
        selected = {ids[i] for i, chosen in enumerate(choices) if chosen}
        if not set(required) <= selected:
            continue
        if any(not set(by_id[name]["requires"]) <= selected
               for name in selected):
            continue
        if any(set(by_id[name]["excludes"]) & selected
               for name in selected):
            continue
        totals = [sum(by_id[name]["cost"][d] for name in selected)
                  for d in range(len(budget))]
        if any(totals[d] > budget[d] for d in range(len(budget))):
            continue
        value = sum(by_id[name]["value"] for name in selected)
        candidate = (-value, tuple(totals), tuple(sorted(selected)))
        if best is None or candidate < best[0]:
            best = (candidate, value, totals)
    if best is None:
        return None
    return {"selected": list(best[0][2]), "value": best[1], "cost": best[2]}


class OptimizerContractTests(unittest.TestCase):
    def test_random_small_instances_match_enumeration(self):
        rng = random.Random(917_431)
        for _case in range(600):
            count = rng.randrange(0, 9)
            dimensions = rng.randrange(1, 4)
            ids = [f"p{i}" for i in range(count)]
            projects = []
            for i, name in enumerate(ids):
                requires = [ids[j] for j in range(i)
                            if rng.random() < 0.12]
                excludes = [ids[j] for j in range(count) if j != i
                            and rng.random() < 0.055]
                projects.append({
                    "id": name,
                    "value": rng.randrange(-4, 10),
                    "cost": [rng.randrange(0, 6)
                             for _ in range(dimensions)],
                    "requires": requires,
                    "excludes": excludes,
                })
            rng.shuffle(projects)
            budget = [rng.randrange(0, 13) for _ in range(dimensions)]
            required = rng.sample(ids, rng.randrange(0, min(3, count) + 1))
            untouched = copy.deepcopy((projects, budget, required))
            expected = brute_force(projects, budget, required)
            actual = solve(projects, budget, required)
            self.assertEqual(actual, expected)
            self.assertEqual((projects, budget, required), untouched)

    def test_multidimensional_cost_tie(self):
        projects = [
            {"id": "a", "value": 5, "cost": [1, 9],
             "requires": [], "excludes": []},
            {"id": "b", "value": 5, "cost": [2, 0],
             "requires": [], "excludes": []},
        ]
        self.assertEqual(
            solve(projects, [2, 9]),
            {"selected": ["a"], "value": 5, "cost": [1, 9]},
        )

    def test_lexicographic_tuple_tie_with_required_project(self):
        projects = [
            {"id": name, "value": 0, "cost": [0],
             "requires": [], "excludes": []}
            for name in ("z", "b", "a", "zz")
        ]
        self.assertEqual(
            solve(projects, [0], ("z",))["selected"],
            ["a", "b", "z"],
        )
        self.assertEqual(solve(projects, [0])["selected"], [])

    def test_one_sided_exclusion_and_infeasible_required_closure(self):
        projects = [
            {"id": "a", "value": 4, "cost": [0],
             "requires": ["b"], "excludes": []},
            {"id": "b", "value": 3, "cost": [0],
             "requires": [], "excludes": ["a"]},
            {"id": "c", "value": 2, "cost": [0],
             "requires": [], "excludes": []},
        ]
        self.assertEqual(
            solve(projects, [0]),
            {"selected": ["b", "c"], "value": 5, "cost": [0]},
        )
        self.assertIsNone(solve(projects, [0], ["a"]))

    def test_validation_categories(self):
        good = {"id": "a", "value": 1, "cost": [0],
                "requires": [], "excludes": []}
        bad_calls = [
            lambda: solve((), [0]),
            lambda: solve([], (0,)),
            lambda: solve([], []),
            lambda: solve([], [0, 0, 0, 0]),
            lambda: solve([], [True]),
            lambda: solve([], [-1]),
            lambda: solve([], [0], "a"),
            lambda: solve([{"id": "a"}], [0]),
            lambda: solve([{**good, "extra": 1}], [0]),
            lambda: solve([{**good, "id": ""}], [0]),
            lambda: solve([good, dict(good)], [0]),
            lambda: solve([{**good, "value": True}], [0]),
            lambda: solve([{**good, "cost": (0,)}], [0]),
            lambda: solve([{**good, "cost": [False]}], [0]),
            lambda: solve([{**good, "requires": ("a",)}], [0]),
            lambda: solve([{**good, "excludes": ["a"]}], [0]),
            lambda: solve([{**good, "requires": ["missing"]}], [0]),
            lambda: solve([good], [0], ["missing"]),
            lambda: solve([good], [0], ["a", "a"]),
        ]
        for number, call in enumerate(bad_calls):
            with self.subTest(number=number), self.assertRaises(ValueError):
                call()

        cycle = [
            {"id": "a", "value": 0, "cost": [0],
             "requires": ["b"], "excludes": []},
            {"id": "b", "value": 0, "cost": [0],
             "requires": ["a"], "excludes": []},
        ]
        with self.assertRaises(ValueError):
            solve(cycle, [0])


if __name__ == "__main__":
    unittest.main()
