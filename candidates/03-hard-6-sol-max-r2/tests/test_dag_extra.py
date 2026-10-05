import threading
import unittest

from dag import run_graph


class GraphExecutorTests(unittest.TestCase):
    def test_invalid_graphs_run_no_functions(self):
        called = []
        valid = {"deps": [], "fn": lambda: called.append("called")}
        cases = [
            {"": valid},
            {0: valid},
            {"a": valid, "b": None},
            {"a": valid, "b": {"fn": lambda: None}},
            {"a": valid, "b": {"deps": (), "fn": lambda: None}},
            {"a": valid, "b": {"deps": [], "fn": None}},
            {"a": valid, "b": {"deps": [3], "fn": lambda: None}},
            {"a": valid, "b": {"deps": ["missing"], "fn": lambda: None}},
            {"a": valid, "b": {"deps": ["a", "a"], "fn": lambda: None}},
            {"a": valid, "b": {"deps": ["b"], "fn": lambda: None}},
            {"a": {"deps": ["b"], "fn": lambda: None},
             "b": {"deps": ["a"], "fn": lambda: None}},
        ]
        for tasks in cases:
            with self.subTest(tasks=tasks), self.assertRaises(ValueError):
                run_graph(tasks)
        self.assertEqual(called, [])

    def test_invalid_worker_counts(self):
        for count in (0, -1, True, False, 1.5, "2", None):
            with self.subTest(count=count), self.assertRaises(ValueError):
                run_graph({}, max_workers=count)
        self.assertEqual(run_graph({}), {})

    def test_failure_skips_descendants_but_not_other_work(self):
        called = []

        def fail():
            called.append("a")
            raise RuntimeError("broken")

        def task(name):
            return lambda: called.append(name) or name

        tasks = {
            "z": {"deps": [], "fn": task("z")},
            "a": {"deps": [], "fn": fail},
            "b": {"deps": ["a"], "fn": task("b")},
            "c": {"deps": ["a", "z"], "fn": task("c")},
            "d": {"deps": ["b", "c"], "fn": task("d")},
            "e": {"deps": ["z"], "fn": task("e")},
        }
        original_deps = {name: list(task["deps"]) for name, task in tasks.items()}

        results = run_graph(tasks)

        self.assertEqual(list(results), sorted(tasks))
        self.assertEqual(results["a"], {"status": "failed", "error": "broken"})
        for name in ("b", "c", "d"):
            self.assertEqual(results[name], {"status": "skipped"})
        self.assertEqual(results["e"], {"status": "completed", "value": "e"})
        self.assertEqual(results["z"], {"status": "completed", "value": "z"})
        self.assertCountEqual(called, ["a", "z", "e"])
        self.assertEqual({name: task["deps"] for name, task in tasks.items()}, original_deps)

    def test_dependent_starts_while_unrelated_task_is_running(self):
        slow_started = threading.Event()
        slow_release = threading.Event()
        dependent_started = threading.Event()
        observed = {}

        def slow():
            slow_started.set()
            if not slow_release.wait(15):
                raise AssertionError("slow task timed out")
            return "slow"

        def fast():
            if not slow_started.wait(5):
                raise AssertionError("independent tasks did not run concurrently")
            return "fast"

        tasks = {
            "a": {"deps": [], "fn": slow},
            "b": {"deps": [], "fn": fast},
            "c": {"deps": ["b"], "fn": lambda: dependent_started.set() or "child"},
        }

        def execute():
            try:
                observed["results"] = run_graph(tasks, max_workers=2)
            except Exception as exc:
                observed["error"] = exc

        thread = threading.Thread(target=execute)
        thread.start()
        try:
            self.assertTrue(slow_started.wait(5))
            self.assertTrue(dependent_started.wait(5))
            self.assertFalse(slow_release.is_set())
        finally:
            slow_release.set()
            thread.join(5)

        self.assertFalse(thread.is_alive())
        self.assertNotIn("error", observed)
        self.assertEqual(observed["results"]["c"],
                         {"status": "completed", "value": "child"})

    def test_long_chain_uses_no_recursion(self):
        tasks = {}
        for index in range(1500):
            name = f"task-{index:04d}"
            predecessor = f"task-{index - 1:04d}"
            tasks[name] = {"deps": [predecessor] if index else [],
                           "fn": lambda: 1}

        results = run_graph(tasks)

        self.assertEqual(len(results), 1500)
        self.assertTrue(all(result == {"status": "completed", "value": 1}
                            for result in results.values()))


if __name__ == "__main__":
    unittest.main()
