import threading
import unittest

from dag import run_graph


class GraphTests(unittest.TestCase):
    def test_invalid_graph_runs_nothing(self):
        called = []
        tasks = {
            "a": {"deps": [], "fn": lambda: called.append("a")},
            "b": {"deps": ["missing"], "fn": lambda: None},
        }
        with self.assertRaises(ValueError):
            run_graph(tasks)
        self.assertEqual(called, [])

    def test_failure_skips_descendants_with_multiple_parents(self):
        called = []

        def fail():
            raise RuntimeError("boom")

        tasks = {
            "a": {"deps": [], "fn": fail},
            "b": {"deps": [], "fn": lambda: 2},
            "c": {"deps": ["a", "b"], "fn": lambda: called.append("c")},
            "d": {"deps": ["c"], "fn": lambda: called.append("d")},
        }
        result = run_graph(tasks)
        self.assertEqual(result["a"], {"status": "failed", "error": "boom"})
        self.assertEqual(result["b"], {"status": "completed", "value": 2})
        self.assertEqual(result["c"], {"status": "skipped"})
        self.assertEqual(result["d"], {"status": "skipped"})
        self.assertEqual(called, [])

    def test_ready_dependent_starts_while_unrelated_task_runs(self):
        slow_started = threading.Event()
        release_slow = threading.Event()
        dependent_started = threading.Event()

        def slow():
            slow_started.set()
            if not release_slow.wait(3):
                raise AssertionError("slow task was not released")

        def fast():
            if not slow_started.wait(3):
                raise AssertionError("independent task did not start")

        def dependent():
            dependent_started.set()

        tasks = {
            "a": {"deps": [], "fn": slow},
            "b": {"deps": [], "fn": fast},
            "c": {"deps": ["b"], "fn": dependent},
        }
        output = []
        runner = threading.Thread(target=lambda: output.append(run_graph(tasks)))
        runner.start()
        try:
            self.assertTrue(dependent_started.wait(2))
        finally:
            release_slow.set()
            runner.join(3)
        self.assertFalse(runner.is_alive())
        self.assertEqual(list(output[0]), ["a", "b", "c"])

    def test_long_chain(self):
        tasks = {
            str(i): {"deps": [str(i - 1)] if i else [], "fn": lambda: None}
            for i in range(1500)
        }
        result = run_graph(tasks)
        self.assertEqual(len(result), 1500)
        self.assertTrue(all(item["status"] == "completed" for item in result.values()))

    def test_bool_workers_rejected(self):
        with self.assertRaises(ValueError):
            run_graph({}, True)
