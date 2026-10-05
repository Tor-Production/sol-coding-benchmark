import threading
import unittest

from dag import run_graph


class GraphTests(unittest.TestCase):
    def test_invalid_graph_never_starts_ready_task(self):
        called = []

        def ready():
            called.append(True)

        invalid_tasks = [
            {"ready": {"deps": [], "fn": ready},
             "bad": {"deps": ["missing"], "fn": lambda: None}},
            {"ready": {"deps": [], "fn": ready},
             "bad": {"deps": ["ready", "ready"], "fn": lambda: None}},
            {"ready": {"deps": [], "fn": ready},
             "a": {"deps": ["b"], "fn": lambda: None},
             "b": {"deps": ["a"], "fn": lambda: None}},
            {"ready": {"deps": [], "fn": ready},
             "bad": {"deps": [], "fn": None}},
            {"ready": {"deps": [], "fn": ready},
             "bad": {"deps": "ready", "fn": lambda: None}},
        ]
        for tasks in invalid_tasks:
            with self.subTest(tasks=tasks), self.assertRaises(ValueError):
                run_graph(tasks)
            self.assertEqual(called, [])

    def test_invalid_ids_and_worker_counts(self):
        for task_id in ("", None, 1):
            with self.subTest(task_id=task_id), self.assertRaises(ValueError):
                run_graph({task_id: {"deps": [], "fn": lambda: None}})
        for workers in (True, False, 0, -1, 1.5, "2"):
            with self.subTest(workers=workers), self.assertRaises(ValueError):
                run_graph({}, max_workers=workers)

    def test_failure_skips_transitive_descendants_with_multiple_parents(self):
        called = []

        def fail():
            raise RuntimeError("boom")

        def must_not_run():
            called.append(True)

        tasks = {
            "f": {"deps": ["d", "b"], "fn": must_not_run},
            "e": {"deps": [], "fn": lambda: 5},
            "d": {"deps": ["c"], "fn": must_not_run},
            "c": {"deps": ["a", "b"], "fn": must_not_run},
            "b": {"deps": [], "fn": lambda: 2},
            "a": {"deps": [], "fn": fail},
        }
        original_deps = {task_id: list(task["deps"]) for task_id, task in tasks.items()}
        result = run_graph(tasks)

        self.assertEqual(list(result), sorted(tasks))
        self.assertEqual(result["a"], {"status": "failed", "error": "boom"})
        self.assertEqual(result["b"], {"status": "completed", "value": 2})
        self.assertEqual(result["e"], {"status": "completed", "value": 5})
        for task_id in ("c", "d", "f"):
            self.assertEqual(result[task_id], {"status": "skipped"})
        self.assertEqual(called, [])
        self.assertEqual(
            {task_id: task["deps"] for task_id, task in tasks.items()}, original_deps
        )

    def test_independent_tasks_run_concurrently(self):
        barrier = threading.Barrier(2)

        def meet():
            barrier.wait(timeout=3)
            return 1

        result = run_graph({
            "a": {"deps": [], "fn": meet},
            "b": {"deps": [], "fn": meet},
        }, max_workers=2)
        self.assertEqual(result["a"], {"status": "completed", "value": 1})
        self.assertEqual(result["b"], {"status": "completed", "value": 1})

    def test_newly_ready_task_starts_while_unrelated_task_runs(self):
        slow_started = threading.Event()
        dependent_started = threading.Event()

        def parent():
            if not slow_started.wait(3):
                raise AssertionError("unrelated task did not start")
            return 1

        def dependent():
            dependent_started.set()
            return 2

        def unrelated():
            slow_started.set()
            return dependent_started.wait(3)

        result = run_graph({
            "a": {"deps": [], "fn": parent},
            "b": {"deps": ["a"], "fn": dependent},
            "z": {"deps": [], "fn": unrelated},
        }, max_workers=2)
        self.assertEqual(result["b"], {"status": "completed", "value": 2})
        self.assertEqual(result["z"], {"status": "completed", "value": True})

    def test_long_chain_is_iterative(self):
        tasks = {}
        for index in range(1500):
            tasks[str(index)] = {
                "deps": [str(index - 1)] if index else [],
                "fn": lambda index=index: index,
            }
        result = run_graph(tasks)
        self.assertEqual(len(result), 1500)
        self.assertEqual(result["1499"], {"status": "completed", "value": 1499})


if __name__ == "__main__":
    unittest.main()
