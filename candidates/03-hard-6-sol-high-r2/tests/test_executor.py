import threading
import unittest

from dag import run_graph


class ExecutorTests(unittest.TestCase):
    def test_invalid_graph_never_starts_work(self):
        calls = []
        valid = {"deps": [], "fn": lambda: calls.append("ran")}
        bad_tasks = [
            {"a": valid, "b": {"deps": ["missing"], "fn": lambda: None}},
            {"a": valid, "b": {"deps": ["a", "a"], "fn": lambda: None}},
            {"a": valid, "b": {"deps": ["b"], "fn": lambda: None}},
            {"a": valid, "b": {"deps": ["c"], "fn": lambda: None},
             "c": {"deps": ["b"], "fn": lambda: None}},
            {"a": valid, "b": {"deps": [], "fn": None}},
            {"a": valid, "b": {"deps": (), "fn": lambda: None}},
            {"a": valid, "b": {}},
            {"a": valid, "": valid},
        ]
        for tasks in bad_tasks:
            with self.subTest(tasks=tasks), self.assertRaises(ValueError):
                run_graph(tasks)
        for workers in (0, -1, True, 1.5, "2"):
            with self.subTest(workers=workers), self.assertRaises(ValueError):
                run_graph({"a": valid}, workers)
        self.assertEqual(calls, [])

    def test_failure_skips_descendants_and_preserves_other_work(self):
        calls = []

        def fail():
            raise RuntimeError("bad")

        tasks = {
            "z": {"deps": [], "fn": lambda: calls.append("z") or 9},
            "a": {"deps": [], "fn": fail},
            "b": {"deps": ["a"], "fn": lambda: calls.append("b")},
            "c": {"deps": ["a", "z"], "fn": lambda: calls.append("c")},
            "d": {"deps": ["b", "c"], "fn": lambda: calls.append("d")},
        }
        result = run_graph(tasks)
        self.assertEqual(list(result), ["a", "b", "c", "d", "z"])
        self.assertEqual(result["a"], {"status": "failed", "error": "bad"})
        self.assertEqual([result[key] for key in ("b", "c", "d")],
                         [{"status": "skipped"}] * 3)
        self.assertEqual(result["z"], {"status": "completed", "value": 9})
        self.assertEqual(calls, ["z"])

    def test_independent_tasks_run_concurrently(self):
        barrier = threading.Barrier(2)

        def meet():
            barrier.wait(timeout=2)
            return "done"

        result = run_graph({"a": {"deps": [], "fn": meet},
                            "b": {"deps": [], "fn": meet}}, max_workers=2)
        self.assertEqual([result[key]["status"] for key in ("a", "b")],
                         ["completed", "completed"])

    def test_newly_ready_task_starts_before_unrelated_slow_task_finishes(self):
        release = threading.Event()

        def slow():
            if not release.wait(timeout=2):
                raise AssertionError("dependent task did not start")
            return "slow"

        def dependent():
            release.set()
            return "dependent"

        tasks = {"a": {"deps": [], "fn": lambda: "fast"},
                 "b": {"deps": ["a"], "fn": dependent},
                 "z": {"deps": [], "fn": slow}}
        result = run_graph(tasks, max_workers=2)
        self.assertEqual([result[key]["status"] for key in ("a", "b", "z")],
                         ["completed"] * 3)

    def test_long_chain_is_iterative(self):
        tasks = {}
        for index in range(1500):
            task_id = f"task{index:04d}"
            deps = [] if index == 0 else [f"task{index - 1:04d}"]
            tasks[task_id] = {"deps": deps, "fn": lambda: 1}
        result = run_graph(tasks)
        self.assertEqual(len(result), 1500)
        self.assertTrue(all(item == {"status": "completed", "value": 1}
                            for item in result.values()))


if __name__ == "__main__":
    unittest.main()
