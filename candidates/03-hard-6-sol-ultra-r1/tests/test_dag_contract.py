import threading
import unittest

from dag import run_graph


class DagContractTests(unittest.TestCase):
    def test_invalid_graph_never_starts_valid_tasks(self):
        bad_tasks = [
            {"": {"deps": [], "fn": lambda: None}},
            {3: {"deps": [], "fn": lambda: None}},
            {"z": None},
            {"z": {"fn": lambda: None}},
            {"z": {"deps": []}},
            {"z": {"deps": (), "fn": lambda: None}},
            {"z": {"deps": [], "fn": None}},
            {"z": {"deps": [""], "fn": lambda: None}},
            {"z": {"deps": [3], "fn": lambda: None}},
            {"z": {"deps": ["a", "a"], "fn": lambda: None}},
            {"z": {"deps": ["missing"], "fn": lambda: None}},
            {"z": {"deps": ["z"], "fn": lambda: None}},
            {"z": {"deps": ["y"], "fn": lambda: None},
             "y": {"deps": ["z"], "fn": lambda: None}},
        ]
        for extra in bad_tasks:
            with self.subTest(extra=extra):
                called = []
                tasks = {"a": {"deps": [], "fn": lambda: called.append("a")}}
                tasks.update(extra)
                with self.assertRaises(ValueError):
                    run_graph(tasks)
                self.assertEqual(called, [])

    def test_invalid_workers_even_for_empty_graph(self):
        for workers in (0, -1, True, False, 1.5, "2", None):
            with self.subTest(workers=workers), self.assertRaises(ValueError):
                run_graph({}, workers)
        self.assertEqual(run_graph({}), {})

    def test_failure_skips_transitive_multi_parent_descendants(self):
        called = []

        def fail():
            raise RuntimeError("boom")

        tasks = {
            "z": {"deps": [], "fn": lambda: "independent"},
            "a": {"deps": [], "fn": fail},
            "b": {"deps": [], "fn": lambda: "ok"},
            "c": {"deps": ["a", "b"], "fn": lambda: called.append("c")},
            "d": {"deps": ["c"], "fn": lambda: called.append("d")},
            "e": {"deps": ["b"], "fn": lambda: "dependent"},
        }
        original_deps = {task_id: task["deps"][:] for task_id, task in tasks.items()}
        result = run_graph(tasks)
        self.assertEqual(list(result), sorted(tasks))
        self.assertEqual(result["a"], {"status": "failed", "error": "boom"})
        self.assertEqual(result["c"], {"status": "skipped"})
        self.assertEqual(result["d"], {"status": "skipped"})
        self.assertEqual(result["b"], {"status": "completed", "value": "ok"})
        self.assertEqual(result["e"], {"status": "completed", "value": "dependent"})
        self.assertEqual(result["z"], {"status": "completed", "value": "independent"})
        self.assertEqual(called, [])
        self.assertEqual({task_id: task["deps"] for task_id, task in tasks.items()}, original_deps)

    def test_newly_ready_task_runs_while_unrelated_task_is_busy(self):
        child_started = threading.Event()

        def slow():
            return child_started.wait(3)

        def child():
            child_started.set()
            return "child"

        result = run_graph({
            "a": {"deps": [], "fn": slow},
            "b": {"deps": [], "fn": lambda: "parent"},
            "c": {"deps": ["b"], "fn": child},
        }, max_workers=2)
        self.assertEqual(result["a"], {"status": "completed", "value": True})
        self.assertEqual(result["c"], {"status": "completed", "value": "child"})

    def test_long_chain(self):
        count = 1500
        tasks = {
            str(i): {"deps": [str(i - 1)] if i else [], "fn": lambda i=i: i}
            for i in range(count)
        }
        result = run_graph(tasks)
        self.assertEqual(len(result), count)
        self.assertEqual(result[str(count - 1)], {"status": "completed", "value": count - 1})


if __name__ == "__main__":
    unittest.main()
