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
        if any(not set(by_id[name]["requires"]) <= selected for name in selected):
            continue
        if any(set(by_id[name]["excludes"]) & selected for name in selected):
            continue
        cost = tuple(sum(by_id[name]["cost"][d] for name in selected)
                     for d in range(len(budget)))
        if any(cost[d] > budget[d] for d in range(len(budget))):
            continue
        value = sum(by_id[name]["value"] for name in selected)
        candidate = (value, cost, tuple(sorted(selected)))
        if (best is None or candidate[0] > best[0]
                or (candidate[0] == best[0] and candidate[1] < best[1])
                or (candidate[:2] == best[:2] and candidate[2] < best[2])):
            best = candidate
    if best is None:
        return None
    return {"selected": list(best[2]), "value": best[0], "cost": list(best[1])}


class ExtraContractTests(unittest.TestCase):
    def test_deterministic_random_instances_against_brute_force(self):
        randomizer = random.Random(731942)
        for case in range(200):
            count = randomizer.randrange(0, 9)
            dimensions = randomizer.randrange(1, 4)
            ids = ["p%02d" % i for i in range(count)]
            projects = []
            for i, name in enumerate(ids):
                # Backward-only requirements guarantee an acyclic graph.
                requires = [ids[j] for j in range(i)
                            if randomizer.random() < 0.12]
                excludes = [ids[j] for j in range(count) if j != i
                            and randomizer.random() < 0.06]
                projects.append({
                    "id": name,
                    "value": randomizer.randrange(-4, 10),
                    "cost": [randomizer.randrange(0, 7)
                             for _ in range(dimensions)],
                    "requires": requires,
                    "excludes": excludes,
                })
            budget = [randomizer.randrange(0, 15) for _ in range(dimensions)]
            required = [name for name in ids if randomizer.random() < 0.08]
            with self.subTest(case=case):
                self.assertEqual(solve(projects, budget, required),
                                 brute_force(projects, budget, required))

    def test_input_is_not_mutated_and_zero_value_id_tie_is_exact(self):
        projects = [
            {"id": "z", "value": 3, "cost": [1], "requires": [], "excludes": []},
            {"id": "a", "value": 0, "cost": [0], "requires": [], "excludes": []},
        ]
        snapshot = repr(projects)
        self.assertEqual(
            solve(projects, [1], ["z"]),
            {"selected": ["a", "z"], "value": 3, "cost": [1]},
        )
        self.assertEqual(repr(projects), snapshot)

    def test_invalid_data_is_checked_even_when_unaffordable(self):
        projects = [
            {"id": "ok", "value": 1, "cost": [1], "requires": [], "excludes": []},
            {"id": "bad", "value": 1, "cost": [100], "requires": ["missing"], "excludes": []},
        ]
        with self.assertRaises(ValueError):
            solve(projects, [1])


if __name__ == "__main__":
    unittest.main()
