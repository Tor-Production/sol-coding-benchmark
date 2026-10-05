import threading
import unittest

from dag import run_graph


class AdditionalTests(unittest.TestCase):
    def test_invalid_graph_runs_nothing(self):
        called = []
        tasks = {
            "root": {"deps": [], "fn": lambda: called.append("root")},
            "bad": {"deps": ["missing"], "fn": lambda: None},
        }

        with self.assertRaises(ValueError):
            run_graph(tasks)
        self.assertEqual(called, [])

    def test_invalid_worker_counts(self):
        for value in (True, False, 0, -1, 1.0, "2", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                run_graph({}, value)

    def test_failure_skips_all_descendants_but_not_other_work(self):
        called = []

        def fail():
            raise RuntimeError("broken")

        tasks = {
            "failed": {"deps": [], "fn": fail},
            "other": {"deps": [], "fn": lambda: called.append("other") or 3},
            "child": {
                "deps": ["failed", "other"],
                "fn": lambda: called.append("child"),
            },
            "grandchild": {
                "deps": ["child"],
                "fn": lambda: called.append("grandchild"),
            },
        }

        result = run_graph(tasks)

        self.assertEqual(result["failed"], {"status": "failed", "error": "broken"})
        self.assertEqual(result["other"], {"status": "completed", "value": 3})
        self.assertEqual(result["child"], {"status": "skipped"})
        self.assertEqual(result["grandchild"], {"status": "skipped"})
        self.assertEqual(called, ["other"])

    def test_newly_ready_task_starts_while_unrelated_task_is_slow(self):
        dependent_started = threading.Event()
        slow_observed_dependent = []

        def slow():
            slow_observed_dependent.append(dependent_started.wait(2))

        def dependent():
            dependent_started.set()

        tasks = {
            "fast": {"deps": [], "fn": lambda: None},
            "slow": {"deps": [], "fn": slow},
            "dependent": {"deps": ["fast"], "fn": dependent},
        }

        run_graph(tasks, max_workers=2)
        self.assertEqual(slow_observed_dependent, [True])

    def test_result_order_is_sorted(self):
        result = run_graph({
            "z": {"deps": [], "fn": lambda: "z"},
            "a": {"deps": [], "fn": lambda: "a"},
            "m": {"deps": [], "fn": lambda: "m"},
        })
        self.assertEqual(list(result), ["a", "m", "z"])

    def test_long_chain_is_iterative(self):
        tasks = {}
        for index in range(1500):
            task_id = str(index)
            deps = [] if index == 0 else [str(index - 1)]
            tasks[task_id] = {"deps": deps, "fn": lambda value=index: value}

        result = run_graph(tasks, max_workers=4)
        self.assertEqual(len(result), 1500)
        self.assertEqual(result["1499"], {"status": "completed", "value": 1499})


if __name__ == "__main__":
    unittest.main()
