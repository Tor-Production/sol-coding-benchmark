import threading
import unittest

from dag import run_graph


class GraphBehaviorTests(unittest.TestCase):
    def test_invalid_graph_runs_no_functions(self):
        called = []
        good = {"deps": [], "fn": lambda: called.append("called")}
        invalid = [
            {"a": good, "b": {"deps": ["missing"], "fn": lambda: None}},
            {"a": good, "b": {"deps": ["b"], "fn": lambda: None}},
            {"a": good, "b": {"deps": ["a", "a"], "fn": lambda: None}},
            {"a": good, "b": {"deps": ["c"], "fn": lambda: None},
             "c": {"deps": ["b"], "fn": lambda: None}},
            {"a": good, "b": {"deps": [], "fn": None}},
            {"a": good, "b": {"deps": (), "fn": lambda: None}},
            {"a": good, "": good},
        ]
        for tasks in invalid:
            with self.subTest(tasks=tasks), self.assertRaises(ValueError):
                run_graph(tasks)
        self.assertEqual(called, [])
        for workers in (0, -1, 1.5, True):
            with self.subTest(workers=workers), self.assertRaises(ValueError):
                run_graph({"a": good}, workers)
        self.assertEqual(called, [])

    def test_failure_skips_descendants_with_multiple_parents(self):
        called = []

        def fail():
            raise RuntimeError("boom")

        tasks = {
            "z": {"deps": [], "fn": lambda: 4},
            "a": {"deps": [], "fn": fail},
            "b": {"deps": [], "fn": lambda: 2},
            "c": {"deps": ["a", "b"], "fn": lambda: called.append("c")},
            "d": {"deps": ["c", "z"], "fn": lambda: called.append("d")},
        }
        result = run_graph(tasks)
        self.assertEqual(list(result), sorted(tasks))
        self.assertEqual(result["a"], {"status": "failed", "error": "boom"})
        self.assertEqual(result["c"], {"status": "skipped"})
        self.assertEqual(result["d"], {"status": "skipped"})
        self.assertEqual(result["z"], {"status": "completed", "value": 4})
        self.assertEqual(called, [])

    def test_ready_dependent_starts_while_unrelated_task_is_running(self):
        release_slow = threading.Event()
        slow_started = threading.Event()
        dependent_started = threading.Event()
        outcome = []

        def slow():
            slow_started.set()
            release_slow.wait(3)

        def dependent():
            dependent_started.set()

        tasks = {
            "a": {"deps": [], "fn": slow},
            "b": {"deps": [], "fn": lambda: None},
            "c": {"deps": ["b"], "fn": dependent},
        }
        coordinator = threading.Thread(target=lambda: outcome.append(run_graph(tasks, 2)))
        coordinator.start()
        try:
            self.assertTrue(slow_started.wait(1))
            self.assertTrue(dependent_started.wait(1))
            self.assertTrue(coordinator.is_alive())
        finally:
            release_slow.set()
            coordinator.join(4)
        self.assertFalse(coordinator.is_alive())
        self.assertEqual(outcome[0]["c"], {"status": "completed", "value": None})

    def test_long_chain(self):
        tasks = {
            str(i): {"deps": [str(i - 1)] if i else [], "fn": lambda: 1}
            for i in range(1500)
        }
        result = run_graph(tasks)
        self.assertEqual(len(result), 1500)
        self.assertTrue(all(entry == {"status": "completed", "value": 1}
                            for entry in result.values()))


if __name__ == "__main__":
    unittest.main()
