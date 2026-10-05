import threading
import unittest

from dag import run_graph


class GraphTests(unittest.TestCase):
    def test_rejects_invalid_graph_before_any_function_runs(self):
        calls = []
        valid = {"a": {"deps": [], "fn": lambda: calls.append("a")}}
        invalid = [
            {**valid, "": {"deps": [], "fn": lambda: None}},
            {**valid, "b": {"deps": ["a", "a"], "fn": lambda: None}},
            {**valid, "b": {"deps": ["missing"], "fn": lambda: None}},
            {**valid, "b": {"deps": ["b"], "fn": lambda: None}},
            {**valid, "b": {"deps": ("a",), "fn": lambda: None}},
            {**valid, "b": {"deps": [], "fn": None}},
            {**valid, "b": {}},
            {**valid, "b": {"deps": ["c"], "fn": lambda: None},
             "c": {"deps": ["b"], "fn": lambda: None}},
        ]
        for graph in invalid:
            with self.subTest(graph=graph):
                with self.assertRaises(ValueError):
                    run_graph(graph)
                self.assertEqual(calls, [])

        for workers in (0, -1, True, 1.5, "2"):
            with self.subTest(workers=workers):
                with self.assertRaises(ValueError):
                    run_graph(valid, workers)
                self.assertEqual(calls, [])

    def test_failures_skip_all_descendants_and_keep_independent_work(self):
        calls = []

        def fail():
            calls.append("fail")
            raise RuntimeError("broken")

        graph = {
            "z": {"deps": ["x", "y"], "fn": lambda: calls.append("z")},
            "x": {"deps": ["a"], "fn": lambda: calls.append("x")},
            "y": {"deps": [], "fn": lambda: calls.append("y")},
            "a": {"deps": [], "fn": fail},
            "zz": {"deps": ["z"], "fn": lambda: calls.append("zz")},
        }
        result = run_graph(graph)
        self.assertEqual(list(result), sorted(graph))
        self.assertEqual(result["a"], {"status": "failed", "error": "broken"})
        self.assertEqual(result["y"], {"status": "completed", "value": None})
        for task_id in ("x", "z", "zz"):
            self.assertEqual(result[task_id], {"status": "skipped"})
        self.assertCountEqual(calls, ["fail", "y"])
        self.assertEqual(graph["z"]["deps"], ["x", "y"])

    def test_ready_tasks_run_concurrently(self):
        rendezvous = threading.Barrier(2)

        def meet():
            rendezvous.wait(timeout=2)
            return 1

        graph = {"a": {"deps": [], "fn": meet},
                 "b": {"deps": [], "fn": meet}}
        result = run_graph(graph, max_workers=2)
        self.assertEqual([entry["status"] for entry in result.values()],
                         ["completed", "completed"])

    def test_newly_ready_task_starts_while_unrelated_task_runs(self):
        release_slow = threading.Event()

        def slow():
            if not release_slow.wait(timeout=2):
                raise AssertionError("dependent did not run while worker was free")

        def child():
            release_slow.set()

        graph = {"a_slow": {"deps": [], "fn": slow},
                 "b_parent": {"deps": [], "fn": lambda: None},
                 "c_child": {"deps": ["b_parent"], "fn": child}}
        result = run_graph(graph, max_workers=2)
        self.assertTrue(all(item["status"] == "completed" for item in result.values()))

    def test_long_chain(self):
        graph = {
            f"task{i:04d}": {
                "deps": [f"task{i - 1:04d}"] if i else [],
                "fn": lambda i=i: i,
            }
            for i in range(1500)
        }
        result = run_graph(graph)
        self.assertEqual(len(result), 1500)
        self.assertEqual(result["task1499"], {"status": "completed", "value": 1499})


if __name__ == "__main__":
    unittest.main()
