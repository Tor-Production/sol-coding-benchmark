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
    by_id = {item["id"]: item for item in projects}
    ids = sorted(by_id)
    forbidden = set()
    for item in projects:
        for other in item["excludes"]:
            forbidden.add(frozenset((item["id"], other)))
    best = None
    for flags in itertools.product((False, True), repeat=len(ids)):
        chosen = {ids[i] for i, flag in enumerate(flags) if flag}
        if not set(required) <= chosen:
            continue
        if any(not set(by_id[name]["requires"]) <= chosen for name in chosen):
            continue
        if any(pair <= chosen for pair in forbidden):
            continue
        costs = [sum(by_id[name]["cost"][d] for name in chosen)
                 for d in range(len(budget))]
        if any(costs[d] > budget[d] for d in range(len(budget))):
            continue
        value = sum(by_id[name]["value"] for name in chosen)
        candidate = (value, tuple(costs), tuple(sorted(chosen)))
        if (best is None or candidate[0] > best[0]
                or (candidate[0] == best[0] and candidate[1] < best[1])
                or (candidate[:2] == best[:2] and candidate[2] < best[2])):
            best = candidate
    if best is None:
        return None
    return {"selected": list(best[2]), "value": best[0], "cost": list(best[1])}


class ExtraContractTests(unittest.TestCase):
    def test_zero_cost_id_tie_with_required_project(self):
        items = [
            project("z", 1, [0]),
            project("a", 0, [0]),
            project("y", 0, [0]),
            project("zz", 0, [0]),
        ]
        self.assertEqual(
            solve(items, [0], ["z"]),
            {"selected": ["a", "y", "z"], "value": 1, "cost": [0]},
        )

    def test_random_small_matches_brute_force(self):
        randomizer = random.Random(94721)
        for _case in range(160):
            n = randomizer.randrange(0, 9)
            dimensions = randomizer.randrange(1, 4)
            ids = ["p%02d" % i for i in range(n)]
            items = []
            for i, name in enumerate(ids):
                requires = [ids[j] for j in range(i)
                            if randomizer.random() < 0.12]
                excludes = [ids[j] for j in range(n) if j != i
                            and randomizer.random() < 0.06]
                items.append(project(
                    name,
                    randomizer.randrange(-4, 10),
                    [randomizer.randrange(0, 6) for _ in range(dimensions)],
                    requires,
                    excludes,
                ))
            budget = [randomizer.randrange(0, 13) for _ in range(dimensions)]
            required = randomizer.sample(ids, randomizer.randrange(0, min(3, n) + 1))
            expected = brute_force(items, budget, required)
            self.assertEqual(solve(items, budget, required), expected)


if __name__ == "__main__":
    unittest.main()
