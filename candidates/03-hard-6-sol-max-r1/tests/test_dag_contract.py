import threading
import unittest

from dag import run_graph


class GraphContractTests(unittest.TestCase):
    def test_invalid_graph_never_starts_a_task(self):
        cases = [
            {"": {"deps": [], "fn": lambda: None}},
            {1: {"deps": [], "fn": lambda: None}},
            {"bad": {"deps": ["ready", "ready"], "fn": lambda: None}},
            {"bad": {"deps": ["missing"], "fn": lambda: None}},
            {"bad": {"deps": ["bad"], "fn": lambda: None}},
            {"bad": {"deps": [1], "fn": lambda: None}},
            {"bad": {"deps": (), "fn": lambda: None}},
            {"bad": {"deps": [], "fn": None}},
            {"bad": {"fn": lambda: None}},
            {"bad": None},
            {
                "bad": {"deps": ["other"], "fn": lambda: None},
                "other": {"deps": ["bad"], "fn": lambda: None},
            },
        ]
        for invalid in cases:
            with self.subTest(invalid=invalid):
                started = []
                tasks = {"ready": {"deps": [], "fn": lambda: started.append(True)}}
                tasks.update(invalid)
                with self.assertRaises(ValueError):
                    run_graph(tasks)
                self.assertEqual(started, [])

        for workers in (True, False, 0, -1, 1.5, "2"):
            with self.subTest(workers=workers):
                with self.assertRaises(ValueError):
                    run_graph({}, workers)
        self.assertEqual(run_graph({}), {})

    def test_failure_skips_descendants_with_multiple_parents(self):
        started = []

        def fail():
            raise RuntimeError("boom")

        def should_not_run():
            started.append("blocked")

        tasks = {
            "e": {"deps": [], "fn": lambda: 5},
            "d": {"deps": ["c"], "fn": should_not_run},
            "c": {"deps": ["a", "b"], "fn": should_not_run},
            "b": {"deps": [], "fn": lambda: 2},
            "a": {"deps": [], "fn": fail},
        }
        original_deps = {name: task["deps"][:] for name, task in tasks.items()}
        result = run_graph(tasks)
        self.assertEqual(list(result), sorted(tasks))
        self.assertEqual(result["a"], {"status": "failed", "error": "boom"})
        self.assertEqual(result["b"], {"status": "completed", "value": 2})
        self.assertEqual(result["c"], {"status": "skipped"})
        self.assertEqual(result["d"], {"status": "skipped"})
        self.assertEqual(result["e"], {"status": "completed", "value": 5})
        self.assertEqual(started, [])
        self.assertEqual({name: task["deps"] for name, task in tasks.items()}, original_deps)

    def test_newly_ready_task_starts_while_unrelated_task_runs(self):
        slow_started = threading.Event()
        dependent_started = threading.Event()

        def slow():
            slow_started.set()
            return dependent_started.wait(3)

        def quick():
            return slow_started.wait(3)

        def dependent():
            dependent_started.set()
            return "done"

        tasks = {
            "a": {"deps": [], "fn": slow},
            "b": {"deps": [], "fn": quick},
            "c": {"deps": ["b"], "fn": dependent},
        }
        result = run_graph(tasks, max_workers=2)
        self.assertEqual(result["a"], {"status": "completed", "value": True})
        self.assertEqual(result["b"], {"status": "completed", "value": True})
        self.assertEqual(result["c"], {"status": "completed", "value": "done"})

    def test_deep_chain(self):
        tasks = {
            f"task-{index:04d}": {
                "deps": [] if index == 0 else [f"task-{index - 1:04d}"],
                "fn": lambda index=index: index,
            }
            for index in range(1500)
        }
        result = run_graph(tasks)
        self.assertEqual(len(result), 1500)
        self.assertEqual(result["task-1499"], {"status": "completed", "value": 1499})


if __name__ == "__main__":
    unittest.main()
