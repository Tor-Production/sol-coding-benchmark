import threading
import unittest

from dag import run_graph


class AdditionalTests(unittest.TestCase):
    def test_deep_chain_is_iterative(self):
        tasks = {}
        for index in range(1500):
            task_id = str(index)
            deps = [] if index == 0 else [str(index - 1)]
            tasks[task_id] = {"deps": deps, "fn": lambda: None}

        result = run_graph(tasks, max_workers=4)

        self.assertEqual(len(result), 1500)
        self.assertTrue(all(item["status"] == "completed" for item in result.values()))

    def test_invalid_graph_runs_nothing(self):
        calls = []
        tasks = {
            "valid": {"deps": [], "fn": lambda: calls.append("called")},
            "invalid": {"deps": ["missing"], "fn": lambda: None},
        }

        with self.assertRaises(ValueError):
            run_graph(tasks)
        self.assertEqual(calls, [])

    def test_failure_skips_transitive_descendants_with_multiple_parents(self):
        calls = []

        def fail():
            raise RuntimeError("boom")

        tasks = {
            "a": {"deps": [], "fn": fail},
            "b": {"deps": [], "fn": lambda: calls.append("b")},
            "c": {"deps": ["a", "b"], "fn": lambda: calls.append("c")},
            "d": {"deps": ["c"], "fn": lambda: calls.append("d")},
        }

        result = run_graph(tasks)

        self.assertEqual(result["a"], {"status": "failed", "error": "boom"})
        self.assertEqual(result["b"]["status"], "completed")
        self.assertEqual(result["c"], {"status": "skipped"})
        self.assertEqual(result["d"], {"status": "skipped"})
        self.assertEqual(calls, ["b"])

    def test_newly_ready_task_does_not_wait_for_slow_unrelated_task(self):
        root_finished = threading.Event()
        dependent_started = threading.Event()

        def root():
            root_finished.set()

        def dependent():
            dependent_started.set()

        def slow():
            root_was_seen = root_finished.wait(1)
            dependent_was_seen = dependent_started.wait(1)
            return root_was_seen and dependent_was_seen

        tasks = {
            "a-root": {"deps": [], "fn": root},
            "b-slow": {"deps": [], "fn": slow},
            "c-dependent": {"deps": ["a-root"], "fn": dependent},
        }

        result = run_graph(tasks, max_workers=2)

        self.assertEqual(result["c-dependent"]["status"], "completed")
        self.assertEqual(result["b-slow"], {"status": "completed", "value": True})
        self.assertEqual(list(result), sorted(tasks))

    def test_bad_max_workers_and_duplicate_deps(self):
        task = {"a": {"deps": [], "fn": lambda: None}}
        for value in (True, False, 0, -1, 1.5, "2", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                run_graph(task, value)

        duplicate = {"a": {"deps": [], "fn": lambda: None},
                     "b": {"deps": ["a", "a"], "fn": lambda: None}}
        with self.assertRaises(ValueError):
            run_graph(duplicate)


if __name__ == "__main__":
    unittest.main()
