import threading
import unittest

from dag import run_graph


class ExecutorTests(unittest.TestCase):
    def test_validation_precedes_execution(self):
        seen = []
        invalid_tasks = [
            {"deps": (), "fn": lambda: None},
            {"deps": [], "fn": None},
            {"deps": ["a", "a"], "fn": lambda: None},
            {"deps": ["missing"], "fn": lambda: None},
            {"deps": ["bad"], "fn": lambda: None},
            {"deps": [[]], "fn": lambda: None},
            {},
        ]
        for invalid in invalid_tasks:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                run_graph({"a": {"deps": [], "fn": lambda: seen.append(1)},
                           "bad": invalid})
        self.assertEqual(seen, [])
        for workers in (True, False, 0, -1, 1.5, "2", None):
            with self.subTest(workers=workers), self.assertRaises(ValueError):
                run_graph({}, workers)
        for task_id in ("", 1, None):
            with self.subTest(task_id=task_id), self.assertRaises(ValueError):
                run_graph({task_id: {"deps": [], "fn": lambda: None}})
        self.assertEqual(run_graph({}), {})

    def test_failure_skips_descendants_with_multiple_parents(self):
        called = []

        def fail():
            raise RuntimeError("broken")

        tasks = {
            "z": {"deps": [], "fn": lambda: 7},
            "a": {"deps": [], "fn": fail},
            "b": {"deps": ["a", "z"], "fn": lambda: called.append("b")},
            "c": {"deps": ["b"], "fn": lambda: called.append("c")},
        }
        deps = {key: list(task["deps"]) for key, task in tasks.items()}
        result = run_graph(tasks)
        self.assertEqual(list(result), ["a", "b", "c", "z"])
        self.assertEqual(result, {
            "a": {"status": "failed", "error": "broken"},
            "b": {"status": "skipped"}, "c": {"status": "skipped"},
            "z": {"status": "completed", "value": 7},
        })
        self.assertEqual(called, [])
        self.assertEqual(deps, {key: task["deps"] for key, task in tasks.items()})

    def test_newly_ready_task_runs_while_unrelated_task_is_waiting(self):
        slow_started = threading.Event()
        dependent_finished = threading.Event()

        def slow():
            slow_started.set()
            return dependent_finished.wait(3)

        def fast():
            if not slow_started.wait(3):
                raise RuntimeError("independent tasks did not run concurrently")

        def dependent():
            dependent_finished.set()
            return 42

        result = run_graph({
            "a": {"deps": [], "fn": slow},
            "b": {"deps": [], "fn": fast},
            "c": {"deps": ["b"], "fn": dependent},
        })
        self.assertEqual(result["a"], {"status": "completed", "value": True})
        self.assertEqual(result["c"], {"status": "completed", "value": 42})

    def test_long_chain_and_long_failure_propagation(self):
        tasks = {str(i): {"deps": [str(i - 1)] if i else [],
                          "fn": lambda i=i: i} for i in range(1500)}
        self.assertEqual(run_graph(tasks)["1499"],
                         {"status": "completed", "value": 1499})

        def fail():
            raise ValueError("root")

        tasks["0"]["fn"] = fail
        result = run_graph(tasks)
        self.assertTrue(all(result[str(i)] == {"status": "skipped"}
                            for i in range(1, 1500)))


if __name__ == "__main__":
    unittest.main()
