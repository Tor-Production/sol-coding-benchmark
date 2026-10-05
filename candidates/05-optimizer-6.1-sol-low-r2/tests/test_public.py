import unittest
from optimizer import solve

def p(name, value, cost, requires=(), excludes=()):
    return dict(id=name, value=value, cost=list(cost), requires=list(requires), excludes=list(excludes))

class Public(unittest.TestCase):
    def test_example(self):
        projects = [p('a', 8, [4], ['b']), p('b', -1, [1]), p('c', 6, [4], excludes=['b'])]
        self.assertEqual(solve(projects, [5]), dict(selected=['a', 'b'], value=7, cost=[5]))
        self.assertEqual(solve(projects, [5], ['c']), dict(selected=['c'], value=6, cost=[4]))
        self.assertIsNone(solve(projects, [5], ['a', 'c']))

    def test_tie_and_empty(self):
        self.assertEqual(solve([p('z', 4, [2]), p('a', 4, [2])], [2])['selected'], ['a'])
        self.assertEqual(solve([], [3, 5]), dict(selected=[], value=0, cost=[0, 0]))

    def test_validation(self):
        with self.assertRaises(ValueError):
            solve([p('a', True, [0])], [1])

if __name__ == '__main__':
    unittest.main()
