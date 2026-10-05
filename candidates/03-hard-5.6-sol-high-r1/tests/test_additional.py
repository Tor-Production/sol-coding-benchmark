import threading
import unittest

from dag import run_graph


class AdditionalTests(unittest.TestCase):
    def test_entire_graph_is_validated_before_execution(self):
        calls = []
        tasks = {
            "ready": {"deps": [], "fn": lambda: calls.append("called")},
            "x": {"deps": ["y"], "fn": lambda: None},
            "y": {"deps": ["x"], "fn": lambda: None},
        }

        with self.assertRaises(ValueError):
            run_graph(tasks)
        self.assertEqual(calls, [])

    def test_failure_skips_descendants_with_multiple_parents(self):
        tasks = {
            "bad": {
                "deps": [],
                "fn": lambda: (_ for _ in ()).throw(RuntimeError("boom")),
            },
            "good": {"deps": [], "fn": lambda: 1},
            "child": {"deps": ["bad", "good"], "fn": lambda: 2},
            "grandchild": {"deps": ["child"], "fn": lambda: 3},
            "unrelated": {"deps": [], "fn": lambda: 4},
        }

        result = run_graph(tasks)
        self.assertEqual(list(result), sorted(tasks))
        self.assertEqual(result["bad"], {"status": "failed", "error": "boom"})
        self.assertEqual(result["child"], {"status": "skipped"})
        self.assertEqual(result["grandchild"], {"status": "skipped"})
        self.assertEqual(
            result["unrelated"], {"status": "completed", "value": 4}
        )

    def test_newly_ready_task_does_not_wait_for_unrelated_task(self):
        child_started = threading.Event()

        def slow():
            if not child_started.wait(2):
                raise AssertionError("dependent was not scheduled promptly")

        tasks = {
            "a_slow": {"deps": [], "fn": slow},
            "b_fast": {"deps": [], "fn": lambda: None},
            "c_child": {"deps": ["b_fast"], "fn": child_started.set},
        }

        result = run_graph(tasks, max_workers=2)
        self.assertEqual(result["a_slow"]["status"], "completed")
        self.assertEqual(result["c_child"]["status"], "completed")

    def test_long_chain_is_iterative(self):
        tasks = {}
        for number in range(1500):
            task_id = str(number)
            deps = [] if number == 0 else [str(number - 1)]
            tasks[task_id] = {"deps": deps, "fn": lambda: None}

        result = run_graph(tasks)
        self.assertEqual(len(result), 1500)
        self.assertTrue(
            all(outcome["status"] == "completed" for outcome in result.values())
        )

    def test_bool_worker_count_is_invalid_even_for_empty_graph(self):
        with self.assertRaises(ValueError):
            run_graph({}, max_workers=True)


if __name__ == "__main__":
    unittest.main()
