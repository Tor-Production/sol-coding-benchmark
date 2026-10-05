import copy
import random
import unittest

from optimizer import _component_mitm, _constrained, _validate, solve


def project(name, value, cost, requires=(), excludes=()):
    return {"id": name, "value": value, "cost": list(cost),
            "requires": list(requires), "excludes": list(excludes)}


def brute(projects, budget, required=()):
    best = None
    ids = [p["id"] for p in projects]
    for mask in range(1 << len(projects)):
        selected = {ids[i] for i in range(len(ids)) if mask & (1 << i)}
        if not set(required) <= selected:
            continue
        if any(not set(p["requires"]) <= selected or
               set(p["excludes"]) & selected
               for p in projects if p["id"] in selected):
            continue
        costs = tuple(sum(p["cost"][d] for p in projects
                          if p["id"] in selected) for d in range(len(budget)))
        if any(costs[d] > budget[d] for d in range(len(budget))):
            continue
        value = sum(p["value"] for p in projects if p["id"] in selected)
        key = (-value, costs, tuple(sorted(selected)))
        if best is None or key < best[0]:
            best = key, {"selected": list(key[2]), "value": value,
                         "cost": list(costs)}
    return None if best is None else best[1]


class Contract(unittest.TestCase):
    def test_random_small_against_enumeration(self):
        rng = random.Random(18273)
        for case in range(250):
            n = rng.randrange(0, 11)
            dims = rng.randrange(1, 4)
            names = [f"p{i:02}" for i in range(n)]
            projects = []
            for i, name in enumerate(names):
                requires = [names[j] for j in range(i)
                            if rng.randrange(9) == 0] if case % 2 else []
                excludes = [names[j] for j in range(n) if j != i and
                            rng.randrange(9) == 0] if case % 3 else []
                projects.append(project(name, rng.randrange(-3, 7),
                                        [rng.randrange(5) for _ in range(dims)],
                                        requires, excludes))
            rng.shuffle(projects)
            budget = [rng.randrange(11) for _ in range(dims)]
            required = rng.sample(names, min(len(names), rng.randrange(3)))
            before = copy.deepcopy((projects, budget, required))
            self.assertEqual(solve(projects, budget, required),
                             brute(projects, budget, required), case)
            data = _validate(projects, budget, required)
            self.assertEqual(_constrained(*data),
                             brute(projects, budget, required), case)
            self.assertEqual((projects, budget, required), before, case)

    def test_zero_rich_ties_against_enumeration(self):
        rng = random.Random(919)
        for case in range(200):
            n = rng.randrange(2, 11)
            names = [f"q{i:02}" for i in range(n)]
            projects = [project(name, rng.randrange(-1, 2),
                                [rng.randrange(2)],
                                [names[j] for j in range(i)
                                 if rng.randrange(8) == 0],
                                [names[j] for j in range(n) if j != i and
                                 rng.randrange(8) == 0])
                        for i, name in enumerate(names)]
            required = rng.sample(names, rng.randrange(3))
            budget = [rng.randrange(4)]
            self.assertEqual(solve(projects, budget, required),
                             brute(projects, budget, required), case)

    def test_component_search_against_enumeration(self):
        rng = random.Random(840)
        for case in range(120):
            n = rng.randrange(2, 11)
            dims = rng.randrange(1, 4)
            names = [f"r{i:02}" for i in range(n)]
            projects = [project(name, rng.randrange(1, 9),
                                [rng.randrange(4) for _ in range(dims)],
                                [names[j] for j in range(i)
                                 if rng.randrange(10) == 0],
                                [names[j] for j in range(n) if j != i and
                                 rng.randrange(10) == 0])
                        for i, name in enumerate(names)]
            budget = [rng.randrange(8) for _ in range(dims)]
            required = rng.sample(names, rng.randrange(3))
            data = _validate(projects, budget, required)
            names_, values, costs, needs, conflicts, closure, limits, forced = data
            core = 0
            for i in range(n):
                if needs[i] or conflicts[i]:
                    core |= (1 << i) | needs[i] | conflicts[i]
            answer = _component_mitm(names_, values, costs, limits, forced,
                                     needs, conflicts, closure, core)
            if answer is not False:
                self.assertEqual(answer, brute(projects, budget, required), case)

    def test_many_constraint_components(self):
        rng = random.Random(209)
        projects = [project(f"s{i:02}", rng.randrange(1, 20),
                            [rng.randrange(1, 8)],
                            excludes=[f"s{i + 1:02}"] if i % 2 == 0 else [])
                    for i in range(18)]
        self.assertEqual(solve(projects, [35]), brute(projects, [35]))
        projects[0]["value"] = -2
        self.assertEqual(solve(projects, [35]), brute(projects, [35]))

    def test_connected_cut_with_cross_constraints(self):
        rng = random.Random(445)
        for dependency in (False, True):
            projects = [project(f"t{i:02}", rng.randrange(1, 15),
                                [rng.randrange(1, 7), rng.randrange(1, 7)],
                                requires=[f"t{i - 1:02}"]
                                if dependency and i else [],
                                excludes=[f"t{i + 1:02}"]
                                if not dependency and i < 16 else [])
                        for i in range(17)]
            budget = [28, 28]
            self.assertEqual(solve(projects, budget),
                             brute(projects, budget), dependency)
            if dependency:
                for item in projects:
                    item["cost"] = [1, 1]
                self.assertEqual(solve(projects, [1, 1]),
                                 brute(projects, [1, 1]))

    def test_validation_edges(self):
        good = project("a", 1, [0])
        cases = [
            ([dict(good, extra=1)], [1], ()),
            ([dict(good, cost=[True])], [1], ()),
            ([dict(good, value=True)], [1], ()),
            ([dict(good, requires=["x"])], [1], ()),
            ([dict(good, excludes=["a"])], [1], ()),
            ([project("a", 1, [0], ["b"]),
              project("b", 1, [0], ["a"])], [1], ()),
            ([good], [True], ()),
            ([good], [1], ["a", "a"]),
            ([good], [1], ["missing"]),
        ]
        for projects, budget, required in cases:
            with self.subTest(projects=projects, budget=budget,
                              required=required):
                with self.assertRaises(ValueError):
                    solve(projects, budget, required)


if __name__ == "__main__":
    unittest.main()
