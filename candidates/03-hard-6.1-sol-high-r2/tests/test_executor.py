import threading
import unittest
from collections import Counter
from collections.abc import Mapping

from dag import run_graph


class ExecutorTests(unittest.TestCase):
    def test_validation_precedes_execution(self):
        calls = []
        valid = {"deps": [], "fn": lambda: calls.append("called")}
        invalid_tasks = [
            None,
            [],
            {"": valid},
            {1: valid},
            {"bad": None},
            {"bad": {}},
            {"bad": {"deps": [], "fn": 1}},
            {"bad": {"deps": (), "fn": lambda: None}},
            {"bad": {"deps": ["missing"], "fn": lambda: None}},
            {"bad": {"deps": ["bad"], "fn": lambda: None}},
            {"bad": {"deps": ["ok", "ok"], "fn": lambda: None}},
            {"bad": {"deps": [[]], "fn": lambda: None}},
            {"bad": {"deps": [None], "fn": lambda: None}},
            {"bad": {"deps": [""], "fn": lambda: None}},
            {"bad": {"deps": ["other"], "fn": lambda: None},
             "other": {"deps": ["bad"], "fn": lambda: None}},
        ]
        for invalid in invalid_tasks:
            with self.subTest(invalid=invalid):
                graph = {"ok": valid, **invalid} if isinstance(invalid, Mapping) else invalid
                with self.assertRaises(ValueError):
                    run_graph(graph)
                self.assertEqual(calls, [])

    def test_worker_validation_and_empty_graph(self):
        for workers in (True, False, 0, -1, 1.5, "2", None):
            with self.subTest(workers=workers):
                with self.assertRaises(ValueError):
                    run_graph({}, workers)
        self.assertEqual(run_graph({}), {})

    def test_failure_skips_shared_and_transitive_descendants(self):
        calls = Counter()

        def task(name, fail=False):
            def invoke():
                calls[name] += 1
                if fail:
                    raise RuntimeError("broken")
                return name
            return invoke

        graph = {
            "z": {"deps": [], "fn": task("z")},
            "fail": {"deps": [], "fn": task("fail", fail=True)},
            "shared": {"deps": ["z", "fail"], "fn": task("shared")},
            "descendant": {"deps": ["shared"], "fn": task("descendant")},
            "independent": {"deps": ["z"], "fn": task("independent")},
        }
        original_deps = {name: list(spec["deps"]) for name, spec in graph.items()}
        result = run_graph(graph)
        self.assertEqual(list(result), sorted(graph))
        self.assertEqual(result, {
            "z": {"status": "completed", "value": "z"},
            "fail": {"status": "failed", "error": "broken"},
            "shared": {"status": "skipped"},
            "descendant": {"status": "skipped"},
            "independent": {"status": "completed", "value": "independent"},
        })
        self.assertEqual(calls, Counter({"z": 1, "fail": 1, "independent": 1}))
        self.assertEqual({name: spec["deps"] for name, spec in graph.items()}, original_deps)

    def test_ready_tasks_overlap_and_respect_worker_limit(self):
        barrier = threading.Barrier(2)
        lock = threading.Lock()
        active = 0
        peak = 0

        def invoke():
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                barrier.wait(timeout=5)
                return "done"
            finally:
                with lock:
                    active -= 1

        graph = {str(i): {"deps": [], "fn": invoke} for i in range(6)}
        result = run_graph(graph, max_workers=2)
        self.assertEqual(peak, 2)
        self.assertTrue(all(item == {"status": "completed", "value": "done"}
                            for item in result.values()))

    def test_dependent_starts_before_unrelated_slow_task_finishes(self):
        slow_started = threading.Event()
        child_started = threading.Event()

        def slow():
            slow_started.set()
            return child_started.wait(timeout=5)

        def fast():
            if not slow_started.wait(timeout=5):
                raise RuntimeError("slow task never started concurrently")
            return "fast"

        def child():
            child_started.set()
            return "child"

        result = run_graph({
            "a_slow": {"deps": [], "fn": slow},
            "b_fast": {"deps": [], "fn": fast},
            "c_child": {"deps": ["b_fast"], "fn": child},
        })
        self.assertEqual(result["a_slow"], {"status": "completed", "value": True})
        self.assertEqual(result["c_child"], {"status": "completed", "value": "child"})

    def test_multiple_successful_parents(self):
        finished = set()

        def parent(name):
            finished.add(name)
            return name

        def child():
            return finished.copy()

        result = run_graph({
            "a": {"deps": [], "fn": lambda: parent("a")},
            "b": {"deps": [], "fn": lambda: parent("b")},
            "c": {"deps": ["a", "b"], "fn": child},
        })
        self.assertEqual(result["c"], {"status": "completed", "value": {"a", "b"}})

    def test_long_chain_success_and_failure(self):
        graph = {str(i): {"deps": [str(i - 1)] if i else [],
                          "fn": lambda i=i: i} for i in range(1500)}
        result = run_graph(graph)
        self.assertEqual(len(result), 1500)
        self.assertEqual(result["1499"], {"status": "completed", "value": 1499})

        def fail():
            raise ValueError("root failed")

        graph["0"]["fn"] = fail
        result = run_graph(graph)
        self.assertEqual(result["0"], {"status": "failed", "error": "root failed"})
        self.assertTrue(all(item == {"status": "skipped"}
                            for name, item in result.items() if name != "0"))


if __name__ == "__main__":
    unittest.main()
