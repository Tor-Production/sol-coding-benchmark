import threading
import unittest

from dag import run_graph


class ExecutorTests(unittest.TestCase):
    def test_validation_precedes_execution(self):
        called = []
        tasks = {
            "a": {"deps": [], "fn": lambda: called.append("a")},
            "b": {"deps": ["missing"], "fn": lambda: None},
        }
        with self.assertRaises(ValueError):
            run_graph(tasks)
        self.assertEqual(called, [])
        for workers in (0, -1, True, 1.0):
            with self.assertRaises(ValueError):
                run_graph({}, workers)

    def test_failure_skips_descendants_but_not_independent_tasks(self):
        def fail():
            raise RuntimeError("boom")

        tasks = {
            "a": {"deps": [], "fn": fail},
            "b": {"deps": [], "fn": lambda: 2},
            "c": {"deps": ["a", "b"], "fn": lambda: 3},
            "d": {"deps": ["c"], "fn": lambda: 4},
        }
        self.assertEqual(run_graph(tasks), {
            "a": {"status": "failed", "error": "boom"},
            "b": {"status": "completed", "value": 2},
            "c": {"status": "skipped"},
            "d": {"status": "skipped"},
        })

    def test_ready_tasks_run_concurrently(self):
        barrier = threading.Barrier(2, timeout=2)

        def rendezvous():
            barrier.wait()
            return 1

        tasks = {name: {"deps": [], "fn": rendezvous} for name in ("a", "b")}
        self.assertTrue(all(result["status"] == "completed"
                            for result in run_graph(tasks, 2).values()))

    def test_long_chain(self):
        tasks = {
            str(index): {"deps": [str(index - 1)] if index else [], "fn": lambda: None}
            for index in range(1500)
        }
        results = run_graph(tasks)
        self.assertEqual(len(results), 1500)
        self.assertTrue(all(result["status"] == "completed" for result in results.values()))


if __name__ == "__main__":
    unittest.main()
