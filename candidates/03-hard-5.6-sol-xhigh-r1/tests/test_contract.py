import threading
import unittest

from dag import run_graph


class ContractTests(unittest.TestCase):
    def test_validation_finishes_before_any_function_runs(self):
        calls = []
        tasks = {
            "valid": {"deps": [], "fn": lambda: calls.append("valid")},
            "invalid": {"deps": ["missing"], "fn": lambda: None},
        }

        with self.assertRaises(ValueError):
            run_graph(tasks)
        self.assertEqual(calls, [])

    def test_invalid_worker_counts_are_rejected_even_for_empty_graph(self):
        for value in (True, False, 0, -1, 1.5, "2", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                run_graph({}, value)

    def test_failure_skips_all_descendants_but_not_independent_work(self):
        calls = []

        def fail():
            raise RuntimeError("broken")

        tasks = {
            "bad": {"deps": [], "fn": fail},
            "good": {"deps": [], "fn": lambda: calls.append("good") or 7},
            "joined": {
                "deps": ["bad", "good"],
                "fn": lambda: calls.append("joined"),
            },
            "descendant": {
                "deps": ["joined"],
                "fn": lambda: calls.append("descendant"),
            },
        }

        results = run_graph(tasks)

        self.assertEqual(results["bad"], {"status": "failed", "error": "broken"})
        self.assertEqual(results["good"], {"status": "completed", "value": 7})
        self.assertEqual(results["joined"], {"status": "skipped"})
        self.assertEqual(results["descendant"], {"status": "skipped"})
        self.assertEqual(calls, ["good"])

    def test_newly_ready_work_does_not_wait_for_slow_unrelated_work(self):
        slow_started = threading.Event()
        release_slow = threading.Event()

        def slow():
            slow_started.set()
            if not release_slow.wait(5):
                raise RuntimeError("dependent did not run while a worker was free")

        def fast():
            if not slow_started.wait(5):
                raise RuntimeError("slow task did not start concurrently")

        def dependent():
            release_slow.set()
            return "ran"

        tasks = {
            "a-slow": {"deps": [], "fn": slow},
            "b-fast": {"deps": [], "fn": fast},
            "c-dependent": {"deps": ["b-fast"], "fn": dependent},
        }

        results = run_graph(tasks, max_workers=2)

        self.assertEqual(results["a-slow"]["status"], "completed")
        self.assertEqual(
            results["c-dependent"], {"status": "completed", "value": "ran"}
        )

    def test_deep_chain_is_iterative_and_results_are_sorted(self):
        count = 1500
        tasks = {}
        for index in reversed(range(count)):
            task_id = f"task-{index:04d}"
            deps = [] if index == 0 else [f"task-{index - 1:04d}"]
            tasks[task_id] = {"deps": deps, "fn": lambda i=index: i}

        results = run_graph(tasks, max_workers=4)

        self.assertEqual(list(results), sorted(tasks))
        self.assertEqual(
            results[f"task-{count - 1:04d}"],
            {"status": "completed", "value": count - 1},
        )


if __name__ == "__main__":
    unittest.main()
