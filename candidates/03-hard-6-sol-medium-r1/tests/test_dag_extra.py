import threading
import unittest

from dag import run_graph


class GraphExecutorTests(unittest.TestCase):
    def test_validation_happens_before_any_function_runs(self):
        calls = []
        good = {"deps": [], "fn": lambda: calls.append("called")}
        invalid = [
            {1: good},
            {"": good},
            {"b": {"deps": ["missing"], "fn": lambda: None}},
            {"b": {"deps": ["b"], "fn": lambda: None}},
            {"b": {"deps": ["a", "a"], "fn": lambda: None}},
            {"b": {"deps": (), "fn": lambda: None}},
            {"b": {"deps": [], "fn": None}},
            {"b": {"deps": ["c"], "fn": lambda: None},
             "c": {"deps": ["b"], "fn": lambda: None}},
        ]
        for extra in invalid:
            with self.subTest(extra=extra):
                with self.assertRaises(ValueError):
                    run_graph({"a": good, **extra})
        for workers in (0, -1, True, 1.5, "2"):
            with self.subTest(workers=workers):
                with self.assertRaises(ValueError):
                    run_graph({"a": good}, workers)
        self.assertEqual(calls, [])

    def test_failure_skips_all_descendants_and_preserves_other_work(self):
        calls = []

        def fail():
            raise RuntimeError("broken")

        tasks = {
            "z": {"deps": [], "fn": lambda: calls.append("z") or 9},
            "a": {"deps": [], "fn": fail},
            "b": {"deps": [], "fn": lambda: calls.append("b") or 2},
            "c": {"deps": ["a", "b"], "fn": lambda: calls.append("c")},
            "d": {"deps": ["c", "b"], "fn": lambda: calls.append("d")},
        }
        result = run_graph(tasks)
        self.assertEqual(list(result), sorted(tasks))
        self.assertEqual(result["a"], {"status": "failed", "error": "broken"})
        self.assertEqual(result["c"], {"status": "skipped"})
        self.assertEqual(result["d"], {"status": "skipped"})
        self.assertEqual(result["z"], {"status": "completed", "value": 9})
        self.assertCountEqual(calls, ["b", "z"])
        self.assertEqual(tasks["d"]["deps"], ["c", "b"])

    def test_newly_ready_task_starts_while_unrelated_task_runs(self):
        fast_started = threading.Event()
        child_started = threading.Event()

        def slow():
            if not fast_started.wait(2):
                raise RuntimeError("independent task did not start")
            if not child_started.wait(2):
                raise RuntimeError("dependent task did not start promptly")
            return "slow"

        def fast():
            fast_started.set()
            return "fast"

        def child():
            child_started.set()
            return "child"

        result = run_graph({
            "a": {"deps": [], "fn": slow},
            "b": {"deps": [], "fn": fast},
            "c": {"deps": ["b"], "fn": child},
        }, max_workers=2)
        self.assertTrue(all(item["status"] == "completed" for item in result.values()))

    def test_long_chain_is_iterative(self):
        tasks = {}
        for index in range(1500):
            task_id = f"n{index:04d}"
            deps = [] if index == 0 else [f"n{index - 1:04d}"]
            tasks[task_id] = {"deps": deps, "fn": lambda: None}
        result = run_graph(tasks)
        self.assertEqual(len(result), 1500)
        self.assertTrue(all(item == {"status": "completed", "value": None}
                            for item in result.values()))


if __name__ == "__main__":
    unittest.main()
