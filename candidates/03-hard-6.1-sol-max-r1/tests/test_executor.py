import threading
import unittest

from dag import run_graph


class ExecutorTests(unittest.TestCase):
    def test_invalid_graphs_do_not_execute_any_functions(self):
        called = []

        def fn():
            called.append(True)

        def task(deps=None):
            return {"deps": [] if deps is None else deps, "fn": fn}

        invalid_graphs = [
            None,
            [],
            42,
            {"": task()},
            {3: task()},
            {None: task()},
            {"bad": None},
            {"bad": []},
            {"bad": {"fn": fn}},
            {"bad": {"deps": []}},
            {"bad": task(())},
            {"bad": task("parent")},
            {"bad": {"deps": [], "fn": None}},
            {"bad": {"deps": [], "fn": 1}},
            {"bad": task(["missing"])},
            {"bad": task(["bad"])},
            {"bad": task([None])},
            {"bad": task([[]])},
            {"bad": task([""])},
            {"bad": task(["parent", "parent"]), "parent": task()},
            {"cycle_a": task(["cycle_b"]), "cycle_b": task(["cycle_a"])},
        ]
        for graph in invalid_graphs:
            if isinstance(graph, dict):
                graph["a_ready"] = task()
            with self.subTest(graph=graph):
                with self.assertRaises(ValueError):
                    run_graph(graph)
                self.assertEqual(called, [])

    def test_invalid_worker_counts_even_for_empty_graphs(self):
        for workers in [False, True, 0, -1, 1.0, "2", None, []]:
            with self.subTest(workers=workers):
                with self.assertRaises(ValueError):
                    run_graph({}, max_workers=workers)
                called = []
                with self.assertRaises(ValueError):
                    run_graph(
                        {"a": {"deps": [], "fn": lambda: called.append(True)}},
                        max_workers=workers,
                    )
                self.assertEqual(called, [])

    def test_empty_graph(self):
        self.assertEqual(run_graph({}), {})

    def test_sorted_results_preserve_values_and_input(self):
        value = object()
        dependencies = ["z", "a"]
        tasks = {
            "m": {"deps": dependencies, "fn": lambda: value},
            "z": {"deps": [], "fn": lambda: None},
            "a": {"deps": [], "fn": lambda: 7},
        }
        original = {task_id: task.copy() for task_id, task in tasks.items()}
        original_deps = {
            task_id: list(task["deps"]) for task_id, task in tasks.items()
        }

        results = run_graph(tasks)

        self.assertEqual(list(results), ["a", "m", "z"])
        self.assertEqual(
            results,
            {
                "a": {"status": "completed", "value": 7},
                "m": {"status": "completed", "value": value},
                "z": {"status": "completed", "value": None},
            },
        )
        self.assertIs(results["m"]["value"], value)
        self.assertEqual(tasks, original)
        self.assertEqual(list(tasks), ["m", "z", "a"])
        for task_id, task in tasks.items():
            self.assertIs(task["deps"], original[task_id]["deps"])
            self.assertEqual(task["deps"], original_deps[task_id])

    def test_failure_skips_transitive_descendants_and_multiple_parent_joins(self):
        called = []

        def fail():
            called.append("failed")
            raise RuntimeError("failure message")

        def record(task_id):
            def fn():
                called.append(task_id)
                return task_id

            return fn

        tasks = {
            "bad": {"deps": [], "fn": fail},
            "good": {"deps": [], "fn": record("good")},
            "child": {"deps": ["bad"], "fn": record("child")},
            "join": {"deps": ["good", "child"], "fn": record("join")},
            "leaf": {"deps": ["join"], "fn": record("leaf")},
            "unrelated": {"deps": ["good"], "fn": record("unrelated")},
        }
        for workers in (1, 3):
            called.clear()
            with self.subTest(workers=workers):
                results = run_graph(tasks, max_workers=workers)
                self.assertEqual(
                    results,
                    {
                        "bad": {"status": "failed", "error": "failure message"},
                        "child": {"status": "skipped"},
                        "good": {"status": "completed", "value": "good"},
                        "join": {"status": "skipped"},
                        "leaf": {"status": "skipped"},
                        "unrelated": {
                            "status": "completed", "value": "unrelated"
                        },
                    },
                )
                self.assertCountEqual(called, ["failed", "good", "unrelated"])

    def test_ready_tasks_run_concurrently_with_worker_limit(self):
        barrier = threading.Barrier(3)
        lock = threading.Lock()
        active = 0
        peak = 0
        called = []

        def fn():
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                called.append(True)
            try:
                barrier.wait(timeout=5)
            finally:
                with lock:
                    active -= 1
            return "done"

        tasks = {str(i): {"deps": [], "fn": fn} for i in range(9)}
        results = run_graph(tasks, max_workers=3)

        self.assertEqual(peak, 3)
        self.assertEqual(active, 0)
        self.assertEqual(len(called), 9)
        self.assertEqual(
            results,
            {str(i): {"status": "completed", "value": "done"} for i in range(9)},
        )

    def test_newly_ready_child_does_not_wait_for_unrelated_slow_task(self):
        slow_started = threading.Event()
        child_finished = threading.Event()

        def slow():
            slow_started.set()
            if not child_finished.wait(timeout=5):
                raise RuntimeError("dependent was held behind unrelated work")
            return "slow finished"

        def fast():
            if not slow_started.wait(timeout=5):
                raise RuntimeError("ready tasks did not run concurrently")
            return "fast finished"

        def child():
            child_finished.set()
            return "child finished"

        results = run_graph(
            {
                "a_slow": {"deps": [], "fn": slow},
                "b_fast": {"deps": [], "fn": fast},
                "c_child": {"deps": ["b_fast"], "fn": child},
            },
            max_workers=2,
        )
        self.assertEqual(
            results,
            {
                "a_slow": {"status": "completed", "value": "slow finished"},
                "b_fast": {"status": "completed", "value": "fast finished"},
                "c_child": {"status": "completed", "value": "child finished"},
            },
        )

    def test_task_waits_for_all_parents(self):
        child_started = threading.Event()
        fast_finished = threading.Event()

        def slow_parent():
            if not fast_finished.wait(timeout=5):
                raise RuntimeError("other parent did not run")
            if child_started.wait(timeout=0.05):
                raise RuntimeError("child ran before all parents completed")
            return "slow"

        def fast_parent():
            fast_finished.set()
            return "fast"

        def child():
            child_started.set()
            return "child"

        results = run_graph(
            {
                "a_slow": {"deps": [], "fn": slow_parent},
                "b_fast": {"deps": [], "fn": fast_parent},
                "c_child": {"deps": ["a_slow", "b_fast"], "fn": child},
            },
            max_workers=3,
        )
        self.assertTrue(child_started.is_set())
        self.assertEqual(
            {task_id: result["status"] for task_id, result in results.items()},
            {"a_slow": "completed", "b_fast": "completed", "c_child": "completed"},
        )

    def test_deep_chain_executes_each_function_once_in_dependency_order(self):
        called = []

        def make_fn(index):
            def fn():
                called.append(index)
                return index

            return fn

        tasks = {
            str(i): {
                "deps": [] if i == 0 else [str(i - 1)],
                "fn": make_fn(i),
            }
            for i in range(1500)
        }

        results = run_graph(tasks)

        self.assertEqual(called, list(range(1500)))
        self.assertEqual(list(results), sorted(tasks))
        self.assertEqual(
            results,
            {str(i): {"status": "completed", "value": i} for i in range(1500)},
        )

    def test_failure_skips_a_deep_chain_without_recursion(self):
        called = []

        def fail():
            called.append(0)
            raise ValueError("root failed")

        tasks = {"0": {"deps": [], "fn": fail}}
        for i in range(1, 1500):
            tasks[str(i)] = {
                "deps": [str(i - 1)],
                "fn": lambda: called.append("blocked"),
            }

        results = run_graph(tasks)

        self.assertEqual(called, [0])
        self.assertEqual(results["0"], {"status": "failed", "error": "root failed"})
        self.assertEqual(len(results), 1500)
        self.assertTrue(
            all(results[str(i)] == {"status": "skipped"} for i in range(1, 1500))
        )


if __name__ == "__main__":
    unittest.main()
