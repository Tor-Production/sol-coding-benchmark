import threading
import unittest

from dag import run_graph


class AdditionalDagTests(unittest.TestCase):
    def test_validation_happens_before_execution(self):
        calls = []

        def called_if_validation_is_incomplete():
            calls.append("called")

        invalid_graphs = [
            {"": {"deps": [], "fn": called_if_validation_is_incomplete}},
            {"a": {"deps": [], "fn": called_if_validation_is_incomplete},
             "b": {"deps": ["missing"], "fn": lambda: None}},
            {"a": {"deps": ["a"], "fn": called_if_validation_is_incomplete}},
            {"a": {"deps": [], "fn": called_if_validation_is_incomplete},
             "b": {"deps": ["a", "a"], "fn": lambda: None}},
            {"a": {"deps": ["b"], "fn": called_if_validation_is_incomplete},
             "b": {"deps": ["a"], "fn": lambda: None}},
            {"a": {"deps": [], "fn": called_if_validation_is_incomplete},
             "b": {"deps": [], "fn": None}},
        ]

        for graph in invalid_graphs:
            with self.subTest(graph=graph):
                with self.assertRaises(ValueError):
                    run_graph(graph)
                self.assertEqual(calls, [])

    def test_rejects_invalid_worker_counts_even_for_empty_graph(self):
        for worker_count in (True, False, 0, -1, 1.5, "2", None):
            with self.subTest(worker_count=worker_count):
                with self.assertRaises(ValueError):
                    run_graph({}, worker_count)

    def test_failure_skips_all_descendants_but_not_unrelated_tasks(self):
        calls = []

        def fail():
            calls.append("fail")
            raise RuntimeError("boom")

        tasks = {
            "failed": {"deps": [], "fn": fail},
            "other_parent": {
                "deps": [],
                "fn": lambda: calls.append("other_parent") or 2,
            },
            "blocked": {
                "deps": ["failed", "other_parent"],
                "fn": lambda: calls.append("blocked"),
            },
            "descendant": {
                "deps": ["blocked"],
                "fn": lambda: calls.append("descendant"),
            },
            "unrelated": {
                "deps": [],
                "fn": lambda: calls.append("unrelated") or 3,
            },
        }

        result = run_graph(tasks, max_workers=3)

        self.assertEqual(list(result), sorted(tasks))
        self.assertEqual(result["failed"], {"status": "failed", "error": "boom"})
        self.assertEqual(result["blocked"], {"status": "skipped"})
        self.assertEqual(result["descendant"], {"status": "skipped"})
        self.assertEqual(result["unrelated"], {"status": "completed", "value": 3})
        self.assertNotIn("blocked", calls)
        self.assertNotIn("descendant", calls)

    def test_newly_ready_work_starts_while_unrelated_work_is_running(self):
        slow_started = threading.Event()
        release_slow = threading.Event()
        child_started = threading.Event()
        holder = {}

        def slow():
            slow_started.set()
            if not release_slow.wait(5):
                raise AssertionError("test did not release slow task")

        def fast_parent():
            if not slow_started.wait(5):
                raise AssertionError("slow task did not start")

        def child():
            child_started.set()

        tasks = {
            "fast_parent": {"deps": [], "fn": fast_parent},
            "slow": {"deps": [], "fn": slow},
            "child": {"deps": ["fast_parent"], "fn": child},
        }

        def execute():
            try:
                holder["result"] = run_graph(tasks, max_workers=2)
            except BaseException as exc:
                holder["error"] = exc

        runner = threading.Thread(target=execute)
        runner.start()
        try:
            self.assertTrue(slow_started.wait(2))
            self.assertTrue(child_started.wait(2))
        finally:
            release_slow.set()
            runner.join(5)

        self.assertFalse(runner.is_alive())
        if "error" in holder:
            raise holder["error"]
        self.assertEqual(holder["result"]["child"]["status"], "completed")

    def test_deep_chain_is_iterative(self):
        tasks = {}
        for index in range(1500):
            task_id = "task_{:04d}".format(index)
            dependency = [] if index == 0 else ["task_{:04d}".format(index - 1)]
            tasks[task_id] = {
                "deps": dependency,
                "fn": lambda index=index: index,
            }

        result = run_graph(tasks, max_workers=4)

        self.assertEqual(
            result["task_1499"], {"status": "completed", "value": 1499}
        )

    def test_input_dependency_lists_are_not_mutated(self):
        dependency_list = ["a"]
        tasks = {
            "a": {"deps": [], "fn": lambda: 1},
            "b": {"deps": dependency_list, "fn": lambda: 2},
        }

        run_graph(tasks)

        self.assertIs(tasks["b"]["deps"], dependency_list)
        self.assertEqual(dependency_list, ["a"])


if __name__ == "__main__":
    unittest.main()
