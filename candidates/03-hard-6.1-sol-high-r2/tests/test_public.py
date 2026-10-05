import unittest
from dag import run_graph


class PublicTests(unittest.TestCase):
    def test_chain(self):
        seen = []
        tasks = {"a": {"deps": [], "fn": lambda: seen.append("a") or 1},
                 "b": {"deps": ["a"], "fn": lambda: seen.append("b") or 2}}
        self.assertEqual(run_graph(tasks)["b"], {"status": "completed", "value": 2})
        self.assertEqual(seen, ["a", "b"])

    def test_cycle_is_rejected(self):
        tasks = {"a": {"deps": ["b"], "fn": lambda: 1},
                 "b": {"deps": ["a"], "fn": lambda: 2}}
        with self.assertRaises(ValueError):
            run_graph(tasks)
