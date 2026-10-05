import itertools
import random
import unittest

from optimizer import solve


def project(name, value, cost, requires=(), excludes=()):
    return dict(id=name, value=value, cost=list(cost),
                requires=list(requires), excludes=list(excludes))


def brute(projects, budget, required=()):
    answer = None
    for flags in itertools.product((False, True), repeat=len(projects)):
        selected = {p['id'] for p, flag in zip(projects, flags) if flag}
        if not set(required) <= selected:
            continue
        if any(not set(p['requires']) <= selected or
               bool(set(p['excludes']) & selected)
               for p in projects if p['id'] in selected):
            continue
        cost = tuple(sum(p['cost'][d] for p in projects if p['id'] in selected)
                     for d in range(len(budget)))
        if any(x > limit for x, limit in zip(cost, budget)):
            continue
        value = sum(p['value'] for p in projects if p['id'] in selected)
        ids = tuple(sorted(selected))
        candidate = (-value, cost, ids)
        if answer is None or candidate < answer:
            answer = candidate
    if answer is None:
        return None
    return dict(selected=list(answer[2]), value=-answer[0],
                cost=list(answer[1]))


class ExtraContract(unittest.TestCase):
    def test_random_small_against_exhaustive(self):
        rng = random.Random(71)
        for _ in range(100):
            n = rng.randrange(1, 9)
            dims = rng.randrange(1, 4)
            projects = []
            for i in range(n):
                projects.append(project(
                    str(i), rng.randrange(-5, 9),
                    [rng.randrange(5) for _ in range(dims)],
                    [str(j) for j in range(i) if rng.randrange(8) == 0],
                    [str(j) for j in range(i) if rng.randrange(9) == 0]))
            budget = [rng.randrange(12) for _ in range(dims)]
            required = [str(i) for i in range(n) if rng.randrange(7) == 0]
            self.assertEqual(solve(projects, budget, required),
                             brute(projects, budget, required))

    def test_unknown_reference(self):
        with self.assertRaises(ValueError):
            solve([project('a', 1, [0], requires=['missing'])], [1])

    def test_equal_weight_32(self):
        projects = [project(f'{i:02}', 1, [1]) for i in range(32)]
        self.assertEqual(solve(projects, [16]),
                         dict(selected=[f'{i:02}' for i in range(16)],
                              value=16, cost=[16]))

    def test_32_project_dependency_chain(self):
        projects = [project(f'{i:02}', 1, [1, 0],
                            requires=[f'{i-1:02}'] if i else [])
                    for i in range(32)]
        self.assertEqual(solve(projects, [20, 0]),
                         dict(selected=[f'{i:02}' for i in range(20)],
                              value=20, cost=[20, 0]))

    def test_zero_cost_tie_can_select_extra_ids(self):
        projects = [project('z', 1, [0]), project('a', 0, [0])]
        self.assertEqual(solve(projects, [0]),
                         dict(selected=['a', 'z'], value=1, cost=[0]))
