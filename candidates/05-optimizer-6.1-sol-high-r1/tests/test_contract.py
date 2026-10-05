import copy
import random
import unittest

from optimizer import solve


def project(name, value=0, cost=(0,), requires=(), excludes=()):
    return dict(id=name, value=value, cost=list(cost),
                requires=list(requires), excludes=list(excludes))


def exhaustive(projects, budget, required):
    best = None
    for mask in range(1 << len(projects)):
        chosen = [p for i, p in enumerate(projects) if mask & (1 << i)]
        names = {p['id'] for p in chosen}
        if not set(required) <= names:
            continue
        if any(not set(p['requires']) <= names or set(p['excludes']) & names
               for p in chosen):
            continue
        cost = tuple(sum(p['cost'][d] for p in chosen) for d in range(len(budget)))
        if any(c > b for c, b in zip(cost, budget)):
            continue
        value = sum(p['value'] for p in chosen)
        key = (-value, cost, tuple(sorted(names)))
        if best is None or key < best:
            best = key
    if best is None:
        return None
    return dict(selected=list(best[2]), value=-best[0], cost=list(best[1]))


class Contract(unittest.TestCase):
    def test_random_against_exhaustive(self):
        rng = random.Random(321879)
        for case in range(240):
            n = rng.randrange(0, 11)
            dimensions = rng.randrange(1, 4)
            names = [f'p{i:02}' for i in range(n)]
            topo = names[:]
            rng.shuffle(topo)
            projects = []
            for i, name in enumerate(topo):
                deps = [x for x in topo[:i] if rng.random() < .19]
                bans = [x for x in names if x != name and rng.random() < .10]
                projects.append(project(name, rng.randrange(-8, 13),
                                        [rng.randrange(0, 6) for _ in range(dimensions)],
                                        deps, bans))
            rng.shuffle(projects)
            budget = [rng.randrange(0, 16) for _ in range(dimensions)]
            required = [x for x in names if rng.random() < .12]
            with self.subTest(case=case):
                self.assertEqual(solve(projects, budget, required),
                                 exhaustive(projects, budget, required))

    def test_zero_cost_id_prefix_ties(self):
        cases = [
            ([project('a'), project('z', 1)], [], ['a', 'z']),
            ([project('a', 1), project('z')], [], ['a']),
            ([project('a'), project('b'), project('z', -2)], ['z'], ['a', 'b', 'z']),
            ([project('a'), project('b'), project('c'), project('d')], ['b'], ['a', 'b']),
        ]
        for projects, required, expected in cases:
            self.assertEqual(solve(projects, [0], required)['selected'], expected)

    def test_dimensions_and_large_integers(self):
        projects = [project('a', 2, [0, 5, 1]), project('b', 2, [1, 0, 0])]
        self.assertEqual(solve(projects, [1, 5, 1])['selected'], ['a', 'b'])
        projects[0]['excludes'] = ['b']
        self.assertEqual(solve(projects, [1, 5, 1])['selected'], ['a'])
        large = 10 ** 100
        self.assertEqual(solve([project('a', large, [large]),
                                project('b', large + 1, [large + 1])], [large + 1]),
                         dict(selected=['b'], value=large + 1, cost=[large + 1]))

    def test_input_unchanged(self):
        projects = [project('z', 7, [2], ['a']), project('a', -1, [1])]
        budget, required = [3], ['z']
        before = copy.deepcopy((projects, budget, required))
        solve(projects, budget, required)
        self.assertEqual((projects, budget, required), before)

    def test_invalid_contract(self):
        good = project('a', 1, [1])
        cases = [
            ((), [1], ()), ([good] * 33, [1], ()),
            ([good], (), ()), ([good], [], ()), ([good], [1] * 4, ()),
            ([good], [True], ()), ([good], [-1], ()), ([good], [1.0], ()),
            ([good], [1], {'a'}), ([good], [1], ['missing']),
            ([good], [1], ['a', 'a']), ([good], [1], [None]),
            ([good, good], [1], ()), ([42], [1], ()),
        ]
        for field, value in [('id', ''), ('id', 3), ('value', True),
                             ('value', 1.5), ('cost', (1,)), ('cost', [False]),
                             ('cost', [-1]), ('cost', []), ('cost', [1, 2]),
                             ('requires', ()), ('requires', ['a']),
                             ('requires', ['b']), ('requires', [[]]),
                             ('excludes', ['a']), ('excludes', [None])]:
            bad = copy.deepcopy(good)
            bad[field] = value
            cases.append(([bad], [1], ()))
        extra = dict(good, extra=0)
        missing = dict(good)
        del missing['cost']
        cases.extend([([extra], [1], ()), ([missing], [1], ())])
        for field in ['requires', 'excludes']:
            bad = dict(good)
            bad[field] = ['b', 'b']
            cases.append(([bad, project('b')], [1], ()))
        # Cycles in unaffordable projects must still be rejected.
        cases.append(([project('a', 1, [100], ['b']),
                       project('b', 1, [100], ['a'])], [0], ()))
        for projects, budget, required in cases:
            with self.subTest(projects=projects, budget=budget, required=required):
                with self.assertRaises(ValueError):
                    solve(projects, budget, required)

    def test_dependency_exclusion_is_valid(self):
        projects = [project('a', 10, [0], ['b'], ['b']), project('b', 2)]
        self.assertEqual(solve(projects, [0])['selected'], ['b'])
        self.assertIsNone(solve(projects, [0], ['a']))

    def test_32_project_structures(self):
        independent = [project(f'p{i:02}', 1, [1]) for i in range(32)]
        self.assertEqual(solve(independent, [16]),
                         dict(selected=[f'p{i:02}' for i in range(16)], value=16, cost=[16]))
        chain = [project(f'p{i:02}', 1, [1], [f'p{i-1:02}'] if i else [])
                 for i in range(32)]
        self.assertEqual(solve(chain, [20])['selected'], [f'p{i:02}' for i in range(20)])
        pairs = [project(f'p{i:02}', 1, [1], excludes=[f'p{i+16:02}'] if i < 16 else [])
                 for i in range(32)]
        self.assertEqual(solve(pairs, [32])['selected'], [f'p{i:02}' for i in range(16)])

    def test_32_projects_against_budget_dynamic_programming(self):
        rng = random.Random(12851)
        projects = [project(f'p{i:02}', rng.randrange(-3, 12),
                            [rng.randrange(5) for _ in range(3)]) for i in range(32)]
        budget = [24, 23, 22]
        # An independent exact oracle indexed by small budget totals. Process
        # IDs backwards so adding a common future prefix preserves ID ordering.
        states = {(0, 0, 0): (0, ())}
        for p in reversed(projects):
            updated = dict(states)
            for cost, (value, ids) in states.items():
                new_cost = tuple(a + b for a, b in zip(cost, p['cost']))
                if any(a > b for a, b in zip(new_cost, budget)):
                    continue
                new = (value + p['value'], (p['id'],) + ids)
                old = updated.get(new_cost)
                if old is None or (-new[0], new[1]) < (-old[0], old[1]):
                    updated[new_cost] = new
            states = updated
        value, cost, ids = min(((-value, cost, ids)
                               for cost, (value, ids) in states.items()))
        self.assertEqual(solve(projects, budget),
                         dict(selected=list(ids), value=-value, cost=list(cost)))


if __name__ == '__main__':
    unittest.main()
