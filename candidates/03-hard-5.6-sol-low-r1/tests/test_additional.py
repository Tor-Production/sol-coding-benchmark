import unittest

from dag import run_graph


class AdditionalTests(unittest.TestCase):
    def test_deep_chain_and_sorted_result(self):
        count = 1500
        tasks = {
            str(i): {
                "deps": [] if i == 0 else [str(i - 1)],
                "fn": lambda i=i: i,
            }
            for i in range(count)
        }
        result = run_graph(tasks, max_workers=4)
        self.assertEqual(result[str(count - 1)]["value"], count - 1)
        self.assertEqual(list(result), sorted(tasks))

    def test_failure_skips_transitive_descendants(self):
        called = []

        def fail():
            raise RuntimeError("boom")

        tasks = {
            "a": {"deps": [], "fn": fail},
            "b": {"deps": [], "fn": lambda: 7},
            "c": {"deps": ["a", "b"], "fn": lambda: called.append("c")},
            "d": {"deps": ["c"], "fn": lambda: called.append("d")},
        }
        self.assertEqual(
            run_graph(tasks),
            {
                "a": {"status": "failed", "error": "boom"},
                "b": {"status": "completed", "value": 7},
                "c": {"status": "skipped"},
                "d": {"status": "skipped"},
            },
        )
        self.assertEqual(called, [])

    def test_invalid_graph_runs_nothing(self):
        called = []
        tasks = {
            "a": {"deps": [], "fn": lambda: called.append("a")},
            "b": {"deps": ["missing"], "fn": lambda: called.append("b")},
        }
        with self.assertRaises(ValueError):
            run_graph(tasks)
        self.assertEqual(called, [])

    def test_invalid_worker_counts(self):
        for value in (True, False, 0, -1, 1.5, "2"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                run_graph({}, value)


if __name__ == "__main__":
    unittest.main()
