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
    by_id = {item["id"]: item for item in projects}
    ids = sorted(by_id)
    best = None
    for mask in range(1 << len(ids)):
        selected = {ids[i] for i in range(len(ids)) if mask & (1 << i)}
        if not set(required) <= selected:
            continue
        if any(not set(by_id[name]["requires"]) <= selected for name in selected):
            continue
        if any(
            excluded in selected
            for name in selected
            for excluded in by_id[name]["excludes"]
        ):
            continue
        cost = [sum(by_id[name]["cost"][d] for name in selected)
                for d in range(len(budget))]
        if any(cost[d] > budget[d] for d in range(len(budget))):
            continue
        value = sum(by_id[name]["value"] for name in selected)
        names = sorted(selected)
        key = (-value, tuple(cost), tuple(names))
        if best is None or key < best[0]:
            best = (key, {"selected": names, "value": value, "cost": cost})
    return None if best is None else best[1]


class ContractTests(unittest.TestCase):
    def test_zero_value_id_tie_can_prefer_an_extra_project(self):
        projects = [
            project("z", 1, [0]),
            project("a", 0, [0]),
            project("zz", 0, [0]),
        ]
        self.assertEqual(
            solve(projects, [0], ["z"]),
            {"selected": ["a", "z"], "value": 1, "cost": [0]},
        )

    def test_small_random_instances_match_exhaustive_search(self):
        rng = random.Random(731942)
        for case in range(100):
            n = rng.randrange(0, 9)
            dimensions = rng.randrange(1, 4)
            ids = [f"p{i}" for i in range(n)]
            projects = []
            for i, name in enumerate(ids):
                requires = [ids[j] for j in range(i) if rng.random() < 0.13]
                excludes = [
                    ids[j] for j in range(n)
                    if j != i and rng.random() < 0.06
                ]
                projects.append(project(
                    name,
                    rng.randrange(-4, 9),
                    [rng.randrange(0, 6) for _ in range(dimensions)],
                    requires,
                    excludes,
                ))
            budget = [rng.randrange(0, 14) for _ in range(dimensions)]
            required = [name for name in ids if rng.random() < 0.08]
            with self.subTest(case=case):
                self.assertEqual(
                    solve(projects, budget, required),
                    brute_force(projects, budget, required),
                )


if __name__ == "__main__":
    unittest.main()
