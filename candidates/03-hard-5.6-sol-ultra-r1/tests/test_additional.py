import threading
import unittest

from dag import run_graph


class AdditionalTests(unittest.TestCase):
    def test_entire_graph_is_validated_before_execution(self):
        called = []
        tasks = {
            "valid": {"deps": [], "fn": lambda: called.append(True)},
            "invalid": {"deps": ["missing"], "fn": lambda: None},
        }

        with self.assertRaises(ValueError):
            run_graph(tasks)
        self.assertEqual(called, [])

    def test_failure_skips_all_descendants_but_not_other_work(self):
        called = []

        def fail():
            raise RuntimeError("boom")

        tasks = {
            "root": {"deps": [], "fn": fail},
            "other": {"deps": [], "fn": lambda: 10},
            "joined": {
                "deps": ["root", "other"],
                "fn": lambda: called.append("joined"),
            },
            "descendant": {
                "deps": ["joined"],
                "fn": lambda: called.append("descendant"),
            },
        }

        result = run_graph(tasks)

        self.assertEqual(result["root"], {"status": "failed", "error": "boom"})
        self.assertEqual(result["other"], {"status": "completed", "value": 10})
        self.assertEqual(result["joined"], {"status": "skipped"})
        self.assertEqual(result["descendant"], {"status": "skipped"})
        self.assertEqual(called, [])

    def test_newly_ready_work_does_not_wait_for_slow_peer(self):
        dependent_started = threading.Event()

        def slow():
            if not dependent_started.wait(2):
                raise AssertionError("dependent did not start while a worker was free")
            return "slow"

        tasks = {
            "a_fast": {"deps": [], "fn": lambda: "fast"},
            "b_slow": {"deps": [], "fn": slow},
            "c_dependent": {
                "deps": ["a_fast"],
                "fn": lambda: dependent_started.set() or "dependent",
            },
        }

        result = run_graph(tasks, max_workers=2)

        self.assertEqual(result["b_slow"]["status"], "completed")
        self.assertEqual(result["c_dependent"]["status"], "completed")

    def test_sorted_output_and_input_is_unchanged(self):
        deps = ["a"]
        tasks = {
            "z": {"deps": deps, "fn": lambda: 2},
            "a": {"deps": [], "fn": lambda: 1},
        }

        result = run_graph(tasks)

        self.assertEqual(list(result), ["a", "z"])
        self.assertEqual(deps, ["a"])

    def test_chain_of_1500_tasks(self):
        tasks = {}
        for index in range(1500):
            task_id = str(index)
            deps = [] if index == 0 else [str(index - 1)]
            tasks[task_id] = {
                "deps": deps,
                "fn": lambda index=index: index,
            }

        result = run_graph(tasks)

        self.assertEqual(
            result["1499"], {"status": "completed", "value": 1499}
        )

    def test_invalid_worker_values_include_bool(self):
        for value in (True, False, 0, -1, 1.5, "2"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    run_graph({}, value)


if __name__ == "__main__":
    unittest.main()
