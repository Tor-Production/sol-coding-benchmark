import threading
import unittest
from types import MappingProxyType

from dag import run_graph


class ValidationTests(unittest.TestCase):
    def test_invalid_graphs_never_run_a_valid_root(self):
        calls = []

        def task(deps=None):
            return {"deps": [] if deps is None else deps, "fn": lambda: calls.append(1)}

        invalid_graphs = [
            {"": task()},
            {1: task()},
            {None: task()},
            {"bad": None},
            {"bad": []},
            {"bad": {}},
            {"bad": {"deps": []}},
            {"bad": {"fn": lambda: None}},
            {"bad": {"deps": (), "fn": lambda: None}},
            {"bad": {"deps": None, "fn": lambda: None}},
            {"bad": {"deps": "a", "fn": lambda: None}},
            {"bad": {"deps": [], "fn": None}},
            {"bad": {"deps": [], "fn": 1}},
            {"bad": task([[]])},
            {"bad": task([None])},
            {"bad": task([1])},
            {"bad": task([""])},
            {"bad": task(["missing"])},
            {"bad": task(["bad"])},
            {"a": task(), "b": task(["a", "a"])},
            {"a": task(["b"]), "b": task(["a"])},
            {"a": task(["c"]), "b": task(["a"]), "c": task(["b"])},
        ]
        for invalid in invalid_graphs:
            with self.subTest(graph=invalid):
                graph = {"00-valid": task()}
                graph.update(invalid)
                with self.assertRaises(ValueError):
                    run_graph(graph)
                self.assertEqual(calls, [])

    def test_invalid_top_level_inputs_and_worker_counts(self):
        for graph in (None, [], (), "tasks", 1):
            with self.subTest(graph=graph), self.assertRaises(ValueError):
                run_graph(graph)

        calls = []
        graph = {"a": {"deps": [], "fn": lambda: calls.append(1)}}
        for workers in (0, -1, 1.0, "2", None, True, False):
            for tasks in ({}, graph):
                with self.subTest(workers=workers, tasks=tasks):
                    with self.assertRaises(ValueError):
                        run_graph(tasks, max_workers=workers)
        self.assertEqual(calls, [])

    def test_empty_graph(self):
        self.assertEqual(run_graph({}), {})


class ExecutionTests(unittest.TestCase):
    def test_sorted_results_exact_values_and_input_preservation(self):
        value = object()
        tasks = {
            "z": {"deps": ["m", "a"], "fn": lambda: value, "extra": "kept"},
            "m": {"deps": [], "fn": lambda: None},
            "a": {"deps": [], "fn": lambda: False},
        }
        original_items = list(tasks.items())
        original_specs = {name: spec.copy() for name, spec in tasks.items()}
        original_deps = {name: list(spec["deps"]) for name, spec in tasks.items()}
        results = run_graph(MappingProxyType(tasks))

        self.assertEqual(list(results), ["a", "m", "z"])
        self.assertEqual(results, {
            "a": {"status": "completed", "value": False},
            "m": {"status": "completed", "value": None},
            "z": {"status": "completed", "value": value},
        })
        self.assertIs(results["z"]["value"], value)
        self.assertEqual(list(tasks.items()), original_items)
        for name, spec in tasks.items():
            self.assertEqual(spec, original_specs[name])
            self.assertIs(spec["deps"], original_specs[name]["deps"])
            self.assertEqual(spec["deps"], original_deps[name])

    def test_diamond_runs_once_after_all_dependencies(self):
        lock = threading.Lock()
        completed = set()
        calls = []
        dependencies = {"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]}

        def make_fn(name, deps):
            def fn():
                with lock:
                    self.assertTrue(set(deps).issubset(completed))
                    calls.append(name)
                    completed.add(name)
                return name
            return fn

        tasks = {
            name: {"deps": deps, "fn": make_fn(name, deps)}
            for name, deps in dependencies.items()
        }
        results = run_graph(tasks, max_workers=3)
        self.assertCountEqual(calls, dependencies)
        self.assertTrue(all(result["status"] == "completed" for result in results.values()))

    def test_failures_skip_transitive_descendants_with_multiple_parents(self):
        calls = []

        def fail():
            calls.append("bad")
            raise RuntimeError("broken task")

        def succeeds(name):
            def fn():
                calls.append(name)
                return name
            return fn

        tasks = {
            "bad": {"deps": [], "fn": fail},
            "child": {"deps": ["bad"], "fn": succeeds("child")},
            "join": {"deps": ["child", "good"], "fn": succeeds("join")},
            "leaf": {"deps": ["join", "bad"], "fn": succeeds("leaf")},
            "good": {"deps": [], "fn": succeeds("good")},
            "independent": {"deps": ["good"], "fn": succeeds("independent")},
        }
        results = run_graph(tasks, max_workers=2)
        self.assertEqual(results["bad"], {"status": "failed", "error": "broken task"})
        for name in ("child", "join", "leaf"):
            self.assertEqual(results[name], {"status": "skipped"})
        for name in ("good", "independent"):
            self.assertEqual(results[name], {"status": "completed", "value": name})
        self.assertCountEqual(calls, ["bad", "good", "independent"])

    def test_independent_roots_overlap_and_respect_worker_limit(self):
        barrier = threading.Barrier(3, timeout=5)
        lock = threading.Lock()
        active = 0
        maximum = 0

        def fn():
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            try:
                barrier.wait()
                return 1
            finally:
                with lock:
                    active -= 1

        tasks = {str(i): {"deps": [], "fn": fn} for i in range(9)}
        results = run_graph(tasks, max_workers=3)
        self.assertEqual(maximum, 3)
        self.assertEqual(active, 0)
        self.assertTrue(all(result == {"status": "completed", "value": 1}
                            for result in results.values()))

    def test_single_worker_executes_every_root(self):
        calls = []
        tasks = {str(i): {"deps": [], "fn": lambda i=i: calls.append(i)}
                 for i in range(8)}
        results = run_graph(tasks, max_workers=1)
        self.assertCountEqual(calls, range(8))
        self.assertTrue(all(result["status"] == "completed" for result in results.values()))

    def test_newly_ready_dependent_starts_while_unrelated_task_is_running(self):
        slow_started = threading.Event()
        child_started = threading.Event()
        release_slow = threading.Event()
        slow_finished = threading.Event()

        def slow():
            slow_started.set()
            try:
                self.assertTrue(child_started.wait(5), "dependent did not start promptly")
                self.assertTrue(release_slow.wait(5))
            finally:
                slow_finished.set()
            return "slow"

        def parent():
            self.assertTrue(slow_started.wait(5))
            return "parent"

        def child():
            try:
                self.assertFalse(slow_finished.is_set())
                child_started.set()
                return "child"
            finally:
                release_slow.set()

        tasks = {
            "a_slow": {"deps": [], "fn": slow},
            "b_parent": {"deps": [], "fn": parent},
            "c_child": {"deps": ["b_parent"], "fn": child},
        }
        results = run_graph(tasks, max_workers=2)
        self.assertEqual(results, {
            "a_slow": {"status": "completed", "value": "slow"},
            "b_parent": {"status": "completed", "value": "parent"},
            "c_child": {"status": "completed", "value": "child"},
        })
        self.assertTrue(slow_finished.is_set())

    def test_long_chain_success_and_failure_are_iterative(self):
        calls = []
        tasks = {
            str(i): {"deps": [str(i - 1)] if i else [],
                     "fn": lambda i=i: calls.append(i) or i}
            for i in range(1500)
        }
        results = run_graph(tasks)
        self.assertEqual(calls, list(range(1500)))
        self.assertEqual(results["1499"], {"status": "completed", "value": 1499})
        self.assertEqual(list(results), sorted(tasks))

        def fail():
            raise ValueError("root failed")

        calls.clear()
        tasks["0"]["fn"] = fail
        results = run_graph(tasks)
        self.assertEqual(calls, [])
        self.assertEqual(results["0"], {"status": "failed", "error": "root failed"})
        self.assertTrue(all(results[str(i)] == {"status": "skipped"}
                            for i in range(1, 1500)))


if __name__ == "__main__":
    unittest.main()
