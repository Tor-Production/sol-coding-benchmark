import threading
import unittest

from dag import run_graph


class GraphContractTests(unittest.TestCase):
    def test_empty_graph(self):
        self.assertEqual(run_graph({}), {})

    def test_invalid_worker_counts_never_run_functions(self):
        calls = []
        tasks = {"a": {"deps": [], "fn": lambda: calls.append("a")}}
        for workers in (0, -1, True, False, 1.5, "2", None):
            for graph in ({}, tasks):
                with self.subTest(workers=workers, empty=not graph):
                    with self.assertRaises(ValueError):
                        run_graph(graph, max_workers=workers)
        self.assertEqual(calls, [])

    def test_malformed_graphs_are_validated_before_any_work(self):
        calls = []

        def valid(deps=None):
            return {"deps": [] if deps is None else deps,
                    "fn": lambda: calls.append("called")}

        invalid_graphs = [
            None, [], 42,
            {"": valid()}, {3: valid()}, {None: valid()},
            {"bad": None}, {"bad": []}, {"bad": {}},
            {"bad": {"deps": []}}, {"bad": {"fn": lambda: None}},
            {"bad": {"deps": [], "fn": 42}},
            {"bad": {"deps": (), "fn": lambda: None}},
            {"bad": {"deps": "a_root", "fn": lambda: None}},
            {"bad": {"deps": None, "fn": lambda: None}},
            {"bad": valid(["unknown"])}, {"bad": valid(["bad"])},
            {"bad": valid(["a_root", "a_root"])},
            {"bad": valid([None])}, {"bad": valid([[]])},
            {"bad": valid([1])}, {"bad": valid([""])},
            {"bad": valid(["other"]), "other": valid(["bad"])},
        ]
        for index, graph in enumerate(invalid_graphs):
            # An unrelated runnable root must not execute before validation.
            if isinstance(graph, dict):
                graph = {"a_root": valid(), **graph}
            with self.subTest(index=index):
                with self.assertRaises(ValueError):
                    run_graph(graph)
                self.assertEqual(calls, [])

    def test_result_order_values_and_input_are_preserved(self):
        marker = object()
        exception_value = RuntimeError("returned, not raised")
        tasks = {
            "z": {"deps": ["a"], "fn": lambda: marker},
            "m": {"deps": [], "fn": lambda: exception_value},
            "a": {"deps": [], "fn": lambda: None},
        }
        originals = {
            task_id: (task, task["deps"], list(task["deps"]), task["fn"])
            for task_id, task in tasks.items()
        }
        results = run_graph(tasks)
        self.assertEqual(list(results), ["a", "m", "z"])
        self.assertEqual(results, {
            "a": {"status": "completed", "value": None},
            "m": {"status": "completed", "value": exception_value},
            "z": {"status": "completed", "value": marker},
        })
        self.assertEqual(list(tasks), ["z", "m", "a"])
        for task_id, (task, deps, contents, fn) in originals.items():
            self.assertIs(tasks[task_id], task)
            self.assertIs(task["deps"], deps)
            self.assertEqual(deps, contents)
            self.assertIs(task["fn"], fn)
            self.assertEqual(set(task), {"deps", "fn"})

    def test_failures_skip_transitive_descendants_with_multiple_parents(self):
        calls = []
        lock = threading.Lock()

        def function(task_id, failure=None):
            def run():
                with lock:
                    calls.append(task_id)
                if failure is not None:
                    raise failure
                return task_id
            return run

        dependencies = {
            "a": [], "b": [], "c": ["a", "b"], "d": ["c"],
            "e": [], "f": ["d", "e"], "g": [], "h": ["g"],
        }
        failures = {"a": RuntimeError("broken"), "e": ValueError()}
        tasks = {
            task_id: {"deps": deps, "fn": function(task_id, failures.get(task_id))}
            for task_id, deps in dependencies.items()
        }
        results = run_graph(tasks, max_workers=3)
        self.assertEqual(results, {
            "a": {"status": "failed", "error": "broken"},
            "b": {"status": "completed", "value": "b"},
            "c": {"status": "skipped"},
            "d": {"status": "skipped"},
            "e": {"status": "failed", "error": ""},
            "f": {"status": "skipped"},
            "g": {"status": "completed", "value": "g"},
            "h": {"status": "completed", "value": "h"},
        })
        self.assertCountEqual(calls, ["a", "b", "e", "g", "h"])

    def test_diamond_waits_for_every_parent_and_calls_each_once(self):
        completed = set()
        calls = []
        lock = threading.Lock()
        dependencies = {"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]}

        def function(task_id):
            def run():
                with lock:
                    self.assertTrue(set(dependencies[task_id]) <= completed)
                    calls.append(task_id)
                    completed.add(task_id)
                return task_id
            return run

        tasks = {task_id: {"deps": deps, "fn": function(task_id)}
                 for task_id, deps in dependencies.items()}
        results = run_graph(tasks)
        self.assertTrue(all(result["status"] == "completed" for result in results.values()))
        self.assertCountEqual(calls, list(tasks))

    def test_independent_tasks_overlap_within_worker_limit(self):
        for workers in (1, 2, 3):
            with self.subTest(workers=workers):
                barrier = threading.Barrier(workers)
                lock = threading.Lock()
                active = 0
                peak = 0

                def run():
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

                tasks = {str(index): {"deps": [], "fn": run}
                         for index in range(workers * 2)}
                results = run_graph(tasks, max_workers=workers)
                self.assertEqual(peak, workers)
                self.assertEqual(active, 0)
                self.assertTrue(all(result == {"status": "completed", "value": "done"}
                                    for result in results.values()))

    def test_newly_ready_task_starts_before_unrelated_slow_task_finishes(self):
        slow_started = threading.Event()
        child_started = threading.Event()
        slow_finished = threading.Event()

        def slow():
            slow_started.set()
            try:
                self.assertTrue(child_started.wait(timeout=5), "dependent was delayed")
                return "slow"
            finally:
                slow_finished.set()

        def fast():
            self.assertTrue(slow_started.wait(timeout=5))
            return "fast"

        def child():
            try:
                self.assertFalse(slow_finished.is_set())
                return "child"
            finally:
                child_started.set()

        tasks = {
            "a_slow": {"deps": [], "fn": slow},
            "b_fast": {"deps": [], "fn": fast},
            "c_child": {"deps": ["b_fast"], "fn": child},
        }
        self.assertEqual(run_graph(tasks, max_workers=2), {
            "a_slow": {"status": "completed", "value": "slow"},
            "b_fast": {"status": "completed", "value": "fast"},
            "c_child": {"status": "completed", "value": "child"},
        })
        self.assertTrue(slow_finished.is_set())

    def test_deep_success_and_failure_chains_are_iterative(self):
        size = 1500
        for fail_root in (False, True):
            with self.subTest(fail_root=fail_root):
                calls = []

                def function(index):
                    def run():
                        calls.append(index)
                        if index == 0 and fail_root:
                            raise RuntimeError("root failed")
                        return index
                    return run

                tasks = {
                    str(index): {"deps": [str(index - 1)] if index else [],
                                 "fn": function(index)}
                    for index in range(size)
                }
                results = run_graph(tasks)
                self.assertEqual(len(results), size)
                self.assertEqual(list(results), sorted(tasks))
                if fail_root:
                    self.assertEqual(calls, [0])
                    self.assertEqual(results["0"], {"status": "failed", "error": "root failed"})
                    self.assertTrue(all(results[str(index)] == {"status": "skipped"}
                                        for index in range(1, size)))
                else:
                    self.assertEqual(calls, list(range(size)))
                    self.assertTrue(all(results[str(index)] == {"status": "completed", "value": index}
                                        for index in range(size)))


if __name__ == "__main__":
    unittest.main()
