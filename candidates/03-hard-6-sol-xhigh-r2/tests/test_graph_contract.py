import threading
import unittest

from dag import run_graph


class GraphContractTests(unittest.TestCase):
    def test_validation_happens_before_any_function_runs(self):
        calls = []
        good = {"deps": [], "fn": lambda: calls.append("called")}
        cases = [
            ({"": good}, 2),
            ({1: good}, 2),
            ({"a": good, "b": {"deps": ["a", "a"], "fn": lambda: None}}, 2),
            ({"a": good, "b": {"deps": ["missing"], "fn": lambda: None}}, 2),
            ({"a": {"deps": ["a"], "fn": lambda: None}}, 2),
            ({"a": {"deps": ["b"], "fn": lambda: None},
              "b": {"deps": ["a"], "fn": lambda: None}}, 2),
            ({"a": good, "b": {"fn": lambda: None}}, 2),
            ({"a": good, "b": {"deps": (), "fn": lambda: None}}, 2),
            ({"a": good, "b": {"deps": [], "fn": None}}, 2),
            ({"a": good}, True),
            ({"a": good}, 0),
            ({"a": good}, 1.5),
        ]
        for tasks, workers in cases:
            with self.subTest(tasks=tasks, workers=workers):
                with self.assertRaises(ValueError):
                    run_graph(tasks, workers)
                self.assertEqual(calls, [])

    def test_failure_skips_all_descendants_and_preserves_input(self):
        called = []

        def fail():
            raise RuntimeError("boom")

        def record(task_id):
            called.append(task_id)
            return task_id

        tasks = {
            "z": {"deps": ["a", "b"], "fn": lambda: record("z")},
            "a": {"deps": [], "fn": fail},
            "b": {"deps": [], "fn": lambda: record("b")},
            "zz": {"deps": ["z"], "fn": lambda: record("zz")},
            "c": {"deps": [], "fn": lambda: record("c")},
        }
        original_deps = {task_id: task["deps"][:] for task_id, task in tasks.items()}
        result = run_graph(tasks)

        self.assertEqual(list(result), sorted(tasks))
        self.assertEqual(result["a"], {"status": "failed", "error": "boom"})
        self.assertEqual(result["z"], {"status": "skipped"})
        self.assertEqual(result["zz"], {"status": "skipped"})
        self.assertEqual(result["b"], {"status": "completed", "value": "b"})
        self.assertEqual(result["c"], {"status": "completed", "value": "c"})
        self.assertCountEqual(called, ["b", "c"])
        self.assertEqual({task_id: task["deps"] for task_id, task in tasks.items()}, original_deps)

    def test_newly_ready_task_starts_while_unrelated_task_is_running(self):
        child_started = threading.Event()

        def slow():
            return child_started.wait(3)

        def child():
            child_started.set()
            return "child"

        result = run_graph({
            "a_slow": {"deps": [], "fn": slow},
            "b_fast": {"deps": [], "fn": lambda: "fast"},
            "c_child": {"deps": ["b_fast"], "fn": child},
        }, max_workers=2)
        self.assertEqual(result["a_slow"], {"status": "completed", "value": True})
        self.assertEqual(result["c_child"], {"status": "completed", "value": "child"})

    def test_long_chain_is_iterative(self):
        tasks = {}
        for index in range(1500):
            task_id = f"t{index:04d}"
            deps = [] if index == 0 else [f"t{index - 1:04d}"]
            tasks[task_id] = {"deps": deps, "fn": lambda index=index: index}
        result = run_graph(tasks)
        self.assertEqual(len(result), 1500)
        self.assertEqual(result["t1499"], {"status": "completed", "value": 1499})


if __name__ == "__main__":
    unittest.main()
