import threading
import unittest

from dag import run_graph


class AdditionalDagTests(unittest.TestCase):
    def test_invalid_later_task_prevents_all_execution(self):
        calls = []
        tasks = {
            "valid": {"deps": [], "fn": lambda: calls.append("called")},
            "invalid": {"deps": ["missing"], "fn": lambda: None},
        }

        with self.assertRaises(ValueError):
            run_graph(tasks)
        self.assertEqual(calls, [])

    def test_failure_skips_transitive_descendants_but_not_other_work(self):
        def fail():
            raise RuntimeError("boom")

        tasks = {
            "failed": {"deps": [], "fn": fail},
            "other": {"deps": [], "fn": lambda: 7},
            "multiple_parents": {
                "deps": ["failed", "other"],
                "fn": lambda: self.fail("blocked task ran"),
            },
            "descendant": {
                "deps": ["multiple_parents"],
                "fn": lambda: self.fail("transitive descendant ran"),
            },
        }

        result = run_graph(tasks)

        self.assertEqual(list(result), sorted(tasks))
        self.assertEqual(result["failed"], {"status": "failed", "error": "boom"})
        self.assertEqual(result["other"], {"status": "completed", "value": 7})
        self.assertEqual(result["multiple_parents"], {"status": "skipped"})
        self.assertEqual(result["descendant"], {"status": "skipped"})

    def test_newly_ready_task_runs_while_unrelated_task_is_slow(self):
        slow_started = threading.Event()
        dependent_started = threading.Event()

        def fast():
            if not slow_started.wait(2):
                raise RuntimeError("slow task did not start concurrently")

        def slow():
            slow_started.set()
            if not dependent_started.wait(2):
                raise RuntimeError("dependent task was not promptly scheduled")

        def dependent():
            dependent_started.set()

        tasks = {
            "a_fast": {"deps": [], "fn": fast},
            "b_slow": {"deps": [], "fn": slow},
            "c_queued_slow": {"deps": [], "fn": slow},
            "z_dependent": {"deps": ["a_fast"], "fn": dependent},
        }

        result = run_graph(tasks, max_workers=2)
        self.assertTrue(all(item["status"] == "completed" for item in result.values()))

    def test_deep_chain_is_iterative_and_input_is_not_mutated(self):
        size = 1500
        tasks = {}
        original_dep_lists = []
        for index in range(size):
            deps = [] if index == 0 else [str(index - 1)]
            original_dep_lists.append(deps)
            tasks[str(index)] = {
                "deps": deps,
                "fn": lambda index=index: index,
            }

        result = run_graph(tasks)

        self.assertEqual(result[str(size - 1)], {"status": "completed", "value": size - 1})
        self.assertTrue(
            all(tasks[str(index)]["deps"] is deps for index, deps in enumerate(original_dep_lists))
        )

    def test_bool_worker_count_is_invalid_even_for_empty_graph(self):
        with self.assertRaises(ValueError):
            run_graph({}, max_workers=True)


if __name__ == "__main__":
    unittest.main()
