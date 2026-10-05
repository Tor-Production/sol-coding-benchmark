import threading
import unittest

from dag import run_graph


class AdditionalTests(unittest.TestCase):
    def test_invalid_disconnected_component_prevents_all_execution(self):
        called = []
        tasks = {
            "root": {"deps": [], "fn": lambda: called.append("root")},
            "cycle-a": {"deps": ["cycle-b"], "fn": lambda: None},
            "cycle-b": {"deps": ["cycle-a"], "fn": lambda: None},
        }

        with self.assertRaises(ValueError):
            run_graph(tasks)
        self.assertEqual(called, [])

    def test_failure_skips_all_descendants_but_not_other_branches(self):
        called = []

        def fail():
            raise RuntimeError("broken")

        tasks = {
            "failed": {"deps": [], "fn": fail},
            "other-parent": {"deps": [], "fn": lambda: "okay"},
            "child": {
                "deps": ["failed", "other-parent"],
                "fn": lambda: called.append("child"),
            },
            "grandchild": {
                "deps": ["child"],
                "fn": lambda: called.append("grandchild"),
            },
            "unrelated": {"deps": [], "fn": lambda: 42},
        }

        result = run_graph(tasks, max_workers=3)

        self.assertEqual(result["failed"], {"status": "failed", "error": "broken"})
        self.assertEqual(result["child"], {"status": "skipped"})
        self.assertEqual(result["grandchild"], {"status": "skipped"})
        self.assertEqual(result["unrelated"], {"status": "completed", "value": 42})
        self.assertEqual(called, [])

    def test_newly_ready_work_does_not_wait_for_slow_unrelated_work(self):
        child_started = threading.Event()

        def slow():
            if not child_started.wait(2):
                raise AssertionError("dependent task did not start promptly")
            return "slow"

        tasks = {
            "fast": {"deps": [], "fn": lambda: "fast"},
            "slow": {"deps": [], "fn": slow},
            "fast-child": {
                "deps": ["fast"],
                "fn": lambda: child_started.set() or "child",
            },
        }

        result = run_graph(tasks, max_workers=2)

        self.assertEqual(result["fast-child"]["status"], "completed")
        self.assertEqual(result["slow"]["status"], "completed")

    def test_sorted_result_and_unchanged_dependency_lists(self):
        z_deps = []
        a_deps = ["z"]
        tasks = {
            "z": {"deps": z_deps, "fn": lambda: "z"},
            "a": {"deps": a_deps, "fn": lambda: "a"},
        }

        result = run_graph(tasks)

        self.assertEqual(list(result), ["a", "z"])
        self.assertEqual(z_deps, [])
        self.assertEqual(a_deps, ["z"])

    def test_iterative_execution_handles_long_chain(self):
        tasks = {}
        for index in range(1500):
            task_id = str(index)
            deps = [] if index == 0 else [str(index - 1)]
            tasks[task_id] = {"deps": deps, "fn": lambda: None}

        result = run_graph(tasks)

        self.assertEqual(len(result), 1500)
        self.assertTrue(
            all(outcome["status"] == "completed" for outcome in result.values())
        )

    def test_invalid_shapes_and_worker_counts_raise_value_error(self):
        fn = lambda: None
        invalid_tasks = [
            None,
            [],
            {"": {"deps": [], "fn": fn}},
            {"a": None},
            {"a": {"fn": fn}},
            {"a": {"deps": (), "fn": fn}},
            {"a": {"deps": [], "fn": object()}},
            {"a": {"deps": ["missing"], "fn": fn}},
            {"a": {"deps": ["a"], "fn": fn}},
            {"a": {"deps": ["b", "b"], "fn": fn},
             "b": {"deps": [], "fn": fn}},
        ]
        for tasks in invalid_tasks:
            with self.subTest(tasks=tasks):
                with self.assertRaises(ValueError):
                    run_graph(tasks)

        for max_workers in (True, False, 0, -1, 1.5, "2", None):
            with self.subTest(max_workers=max_workers):
                with self.assertRaises(ValueError):
                    run_graph({}, max_workers=max_workers)


if __name__ == "__main__":
    unittest.main()
