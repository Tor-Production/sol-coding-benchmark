import threading
import unittest

from dag import run_graph


class GraphExecutorTests(unittest.TestCase):
    def test_rejects_invalid_graph_before_any_function_runs(self):
        called = []
        cases = [
            {"good": {"deps": [], "fn": lambda: called.append("good")},
             "": {"deps": [], "fn": lambda: None}},
            {"good": {"deps": [], "fn": lambda: called.append("good")},
             1: {"deps": [], "fn": lambda: None}},
            {"good": {"deps": [], "fn": lambda: called.append("good")},
             "bad": {"deps": ["missing"], "fn": lambda: None}},
            {"good": {"deps": [], "fn": lambda: called.append("good")},
             "bad": {"deps": ["good", "good"], "fn": lambda: None}},
            {"good": {"deps": [], "fn": lambda: called.append("good")},
             "bad": {"deps": ["bad"], "fn": lambda: None}},
            {"good": {"deps": [], "fn": lambda: called.append("good")},
             "bad": {"deps": [], "fn": None}},
            {"good": {"deps": [], "fn": lambda: called.append("good")},
             "bad": {"deps": (), "fn": lambda: None}},
            {"good": {"deps": ["bad"], "fn": lambda: called.append("good")},
             "bad": {"deps": ["good"], "fn": lambda: None}},
        ]
        for tasks in cases:
            with self.subTest(tasks=tasks), self.assertRaises(ValueError):
                run_graph(tasks)
            self.assertEqual(called, [])

        for workers in (True, False, 0, -1, 1.5, "2"):
            with self.subTest(workers=workers), self.assertRaises(ValueError):
                run_graph({}, workers)
        with self.assertRaises(ValueError):
            run_graph([])

    def test_failure_skips_descendants_with_multiple_parents(self):
        called = []

        def fail():
            raise RuntimeError("boom")

        tasks = {
            "a": {"deps": [], "fn": fail},
            "b": {"deps": [], "fn": lambda: 2},
            "c": {"deps": ["a", "b"], "fn": lambda: called.append("c")},
            "d": {"deps": ["c"], "fn": lambda: called.append("d")},
            "e": {"deps": [], "fn": lambda: 5},
        }
        original_deps = {task_id: task["deps"][:] for task_id, task in tasks.items()}
        result = run_graph(tasks)
        self.assertEqual(list(result), sorted(tasks))
        self.assertEqual(result["a"], {"status": "failed", "error": "boom"})
        self.assertEqual(result["b"], {"status": "completed", "value": 2})
        self.assertEqual(result["c"], {"status": "skipped"})
        self.assertEqual(result["d"], {"status": "skipped"})
        self.assertEqual(result["e"], {"status": "completed", "value": 5})
        self.assertEqual(called, [])
        self.assertEqual(original_deps,
                         {task_id: task["deps"] for task_id, task in tasks.items()})

    def test_dependent_starts_while_unrelated_work_is_running(self):
        child_started = threading.Event()

        def slow():
            return child_started.wait(2)

        def child():
            child_started.set()
            return "child"

        tasks = {
            "a": {"deps": [], "fn": slow},
            "b": {"deps": [], "fn": lambda: "parent"},
            "c": {"deps": ["b"], "fn": child},
        }
        result = run_graph(tasks, max_workers=2)
        self.assertEqual(result["a"], {"status": "completed", "value": True})
        self.assertEqual(result["c"], {"status": "completed", "value": "child"})

    def test_long_chain(self):
        tasks = {}
        for index in range(1500):
            task_id = f"task-{index:04d}"
            deps = [f"task-{index - 1:04d}"] if index else []
            tasks[task_id] = {"deps": deps, "fn": lambda index=index: index}
        result = run_graph(tasks)
        self.assertEqual(len(result), 1500)
        self.assertEqual(result["task-1499"],
                         {"status": "completed", "value": 1499})


if __name__ == "__main__":
    unittest.main()
