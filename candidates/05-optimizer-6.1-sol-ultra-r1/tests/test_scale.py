import random
import unittest

from optimizer import solve


class Scale(unittest.TestCase):
    def test_32_independent_projects_with_three_large_costs(self):
        rng = random.Random(91027)
        items = [dict(id='p%02d' % i, value=1 << i,
                      cost=[1 << i, rng.randrange(10 ** 11, 10 ** 12),
                            rng.randrange(10 ** 11, 10 ** 12)],
                      requires=[], excludes=[]) for i in range(32)]
        chosen = items[::2]
        budget = [sum(p['cost'][j] for p in chosen) for j in range(3)]
        # Value equals the first cost, so budget[0] is a rigorous upper bound.
        # Binary costs make this attainable subset unique.
        self.assertEqual(solve(items, budget),
                         dict(selected=[p['id'] for p in chosen],
                              value=budget[0], cost=budget))

    def test_32_project_dependency_star(self):
        items = [dict(id='base', value=-7, cost=[3], requires=[], excludes=[])]
        items += [dict(id='p%02d' % i, value=i + 1, cost=[1],
                       requires=['base'], excludes=[]) for i in range(31)]
        self.assertEqual(solve(items, [18]),
                         dict(selected=['base'] + ['p%02d' % i for i in range(16, 31)],
                              value=sum(range(17, 32)) - 7, cost=[18]))

    def test_32_project_dependency_chain(self):
        items = [dict(id='p%02d' % i, value=1, cost=[1],
                      requires=['p%02d' % (i - 1)] if i else [], excludes=[])
                 for i in range(32)]
        self.assertEqual(solve(items, [19]),
                         dict(selected=['p%02d' % i for i in range(19)],
                              value=19, cost=[19]))

    def test_32_project_weighted_conflict_path_against_dynamic_program(self):
        rng = random.Random(8317)
        items = [dict(id='p%02d' % i, value=rng.randrange(1, 20),
                      cost=[rng.randrange(0, 8)], requires=[],
                      excludes=['p%02d' % (i + 1)] if i < 31 else [])
                 for i in range(32)]
        budget = 48
        # An independent path-specific oracle: remember only whether the
        # immediately preceding project was selected and the total cost.
        states = {(0, False): (0, ())}
        for item in items:
            next_states = {}
            for (cost, previous), (value, names) in states.items():
                options = [(cost, False, value, names)]
                if not previous and cost + item['cost'][0] <= budget:
                    options.append((cost + item['cost'][0], True,
                                    value + item['value'], names + (item['id'],)))
                for c, selected, v, ids in options:
                    old = next_states.get((c, selected))
                    if old is None or (-v, ids) < (-old[0], old[1]):
                        next_states[c, selected] = v, ids
            states = next_states
        value, cost, names = min(((-v, c, ids)
                                 for (c, previous), (v, ids) in states.items()))
        self.assertEqual(solve(items, [budget]),
                         dict(selected=list(names), value=-value, cost=[cost]))

    def test_32_free_zero_value_projects(self):
        items = [dict(id='p%02d' % i, value=0, cost=[0, 0, 0],
                      requires=[], excludes=[]) for i in range(32)]
        self.assertEqual(solve(items, [0, 0, 0]),
                         dict(selected=[], value=0, cost=[0, 0, 0]))
        self.assertEqual(solve(items, [0, 0, 0], ['p16']),
                         dict(selected=['p%02d' % i for i in range(17)],
                              value=0, cost=[0, 0, 0]))


if __name__ == '__main__':
    unittest.main()
