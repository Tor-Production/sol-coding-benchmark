import threading
import unittest

from dag import run_graph


class AdditionalTests(unittest.TestCase):
    def test_entire_graph_is_validated_before_execution(self):
        called = []
        tasks = {
            "a": {"deps": [], "fn": lambda: called.append("a")},
            "z": {"deps": ["missing"], "fn": lambda: None},
        }
        with self.assertRaises(ValueError):
            run_graph(tasks)
        self.assertEqual(called, [])

    def test_newly_ready_work_does_not_wait_for_unrelated_work(self):
        release_slow_task = threading.Event()

        def slow():
            return release_slow_task.wait(2)

        def dependent():
            release_slow_task.set()
            return "started"

        tasks = {
            "a": {"deps": [], "fn": lambda: "done"},
            "b": {"deps": ["a"], "fn": dependent},
            "z": {"deps": [], "fn": slow},
        }
        results = run_graph(tasks, max_workers=2)
        self.assertEqual(results["b"]["value"], "started")
        self.assertTrue(results["z"]["value"])

    def test_failure_skips_all_descendants_but_not_independent_work(self):
        def fail():
            raise RuntimeError("boom")

        tasks = {
            "fail": {"deps": [], "fn": fail},
            "child": {"deps": ["fail"], "fn": lambda: "not run"},
            "grandchild": {"deps": ["child"], "fn": lambda: "not run"},
            "other": {"deps": [], "fn": lambda: 42},
        }
        results = run_graph(tasks)
        self.assertEqual(results["fail"], {"status": "failed", "error": "boom"})
        self.assertEqual(results["child"], {"status": "skipped"})
        self.assertEqual(results["grandchild"], {"status": "skipped"})
        self.assertEqual(results["other"], {"status": "completed", "value": 42})

    def test_deep_chain_is_iterative(self):
        tasks = {}
        for index in range(1500):
            task_id = f"task-{index:04d}"
            deps = [] if index == 0 else [f"task-{index - 1:04d}"]
            tasks[task_id] = {"deps": deps, "fn": lambda value=index: value}
        results = run_graph(tasks)
        self.assertEqual(results["task-1499"]["value"], 1499)


if __name__ == "__main__":
    unittest.main()
