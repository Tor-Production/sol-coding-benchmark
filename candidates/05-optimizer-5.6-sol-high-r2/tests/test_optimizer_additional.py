import itertools
import random
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


def brute_force(projects, budget, required=()):
    best = None
    names = [item["id"] for item in projects]
    for chosen_bits in itertools.product((False, True), repeat=len(projects)):
        chosen = {names[i] for i, flag in enumerate(chosen_bits) if flag}
        if not set(required) <= chosen:
            continue
        if any(not set(item["requires"]) <= chosen for item in projects
               if item["id"] in chosen):
            continue
        if any(set(item["excludes"]) & chosen for item in projects
               if item["id"] in chosen):
            continue
        total = [sum(item["cost"][d] for item in projects
                     if item["id"] in chosen) for d in range(len(budget))]
        if any(total[d] > budget[d] for d in range(len(budget))):
            continue
        value = sum(item["value"] for item in projects
                    if item["id"] in chosen)
        result = {"selected": sorted(chosen), "value": value, "cost": total}
        key = (-value, tuple(total), tuple(result["selected"]))
        if best is None or key < best[0]:
            best = (key, result)
    return best[1] if best else None


class AdditionalContractTests(unittest.TestCase):
    def test_zero_value_ids_can_decide_final_tie(self):
        projects = [project("z", 2, [0]), project("a", 0, [0])]
        self.assertEqual(
            solve(projects, [0]),
            {"selected": ["a", "z"], "value": 2, "cost": [0]},
        )

    def test_inputs_are_not_mutated(self):
        projects = [project("a", 3, [1], excludes=["b"]),
                    project("b", 4, [1])]
        snapshot = repr(projects)
        solve(projects, [1], ("a",))
        self.assertEqual(repr(projects), snapshot)

    def test_deterministic_small_instances_against_brute_force(self):
        rng = random.Random(81723)
        for size in range(1, 9):
            for _ in range(25):
                names = [chr(ord("a") + i) for i in range(size)]
                projects = []
                for i, name in enumerate(names):
                    # Earlier-only dependencies guarantee an acyclic graph.
                    requires = [other for other in names[:i]
                                if rng.randrange(7) == 0]
                    excludes = [other for other in names
                                if other != name and rng.randrange(13) == 0]
                    projects.append(project(
                        name, rng.randrange(-3, 8),
                        [rng.randrange(5), rng.randrange(5)],
                        requires, excludes,
                    ))
                budget = [rng.randrange(3, 11), rng.randrange(3, 11)]
                required = tuple(name for name in names
                                 if rng.randrange(17) == 0)
                self.assertEqual(
                    solve(projects, budget, required),
                    brute_force(projects, budget, required),
                )

    def test_large_integers_do_not_require_floats(self):
        huge = 10 ** 1000
        projects = [project("a", huge, [huge]),
                    project("b", huge - 1, [huge - 1])]
        self.assertEqual(solve(projects, [huge])["selected"], ["a"])

    def test_structured_32_project_scale_cases(self):
        equal = [project("p%02d" % i, 1, [1]) for i in range(32)]
        self.assertEqual(solve(equal, [16])["selected"],
                         ["p%02d" % i for i in range(16)])
        zeros = [project("p%02d" % i, 0, [0, 0, 0]) for i in range(32)]
        self.assertEqual(solve(zeros, [0, 0, 0])["selected"], [])

        rng = random.Random(493)
        multidimensional = [
            project("p%02d" % i, rng.randrange(-5, 30),
                    [rng.randrange(20), rng.randrange(20), rng.randrange(20)])
            for i in range(32)
        ]
        result = solve(multidimensional, [100, 100, 100])
        self.assertLessEqual(max(result["cost"]), 100)

        paired = []
        for i in range(16):
            paired.append(project("d%02d" % i, -9, [0]))
            paired.append(project("x%02d" % i, 10, [0], ["d%02d" % i]))
        paired_result = solve(paired, [0])
        self.assertEqual(paired_result["value"], 16)
        self.assertEqual(len(paired_result["selected"]), 32)


if __name__ == "__main__":
    unittest.main()
