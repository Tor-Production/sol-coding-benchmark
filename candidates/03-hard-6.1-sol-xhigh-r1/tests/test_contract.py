import threading
import unittest
from types import MappingProxyType

from dag import run_graph


class ContractTests(unittest.TestCase):
    def test_empty_graph(self):
        self.assertEqual(run_graph({}), {})

    def test_invalid_worker_counts_are_rejected_before_execution(self):
        calls = []
        tasks = {"a": {"deps": [], "fn": lambda: calls.append("a")}}
        for workers in (0, -1, True, False, 1.0, "2", None):
            with self.subTest(workers=workers):
                with self.assertRaises(ValueError):
                    run_graph(tasks, max_workers=workers)
                with self.assertRaises(ValueError):
                    run_graph({}, max_workers=workers)
        self.assertEqual(calls, [])

    def test_invalid_graphs_are_rejected_before_execution(self):
        calls = []
        valid = {"deps": [], "fn": lambda: calls.append("called")}
        malformed = [
            None,
            [],
            "task",
            {},
            {"deps": []},
            {"fn": valid["fn"]},
            {"deps": (), "fn": valid["fn"]},
            {"deps": "a", "fn": valid["fn"]},
            {"deps": None, "fn": valid["fn"]},
            {"deps": {}, "fn": valid["fn"]},
            {"deps": [], "fn": None},
            {"deps": [], "fn": 42},
        ]
        invalid_graphs = [None, [], (), "tasks", 42]
        invalid_graphs.extend({"a": valid, "z": task} for task in malformed)
        invalid_graphs.extend({"a": valid, task_id: valid}
                              for task_id in ("", None, 1, False))
        invalid_graphs.extend(
            {"a": valid, "z": {"deps": deps, "fn": valid["fn"]}}
            for deps in (["a", "a"], ["unknown"], ["z"], [""], [None],
                         [1], [[]], [{}])
        )
        invalid_graphs.append({
            "a": valid,
            "y": {"deps": ["z"], "fn": valid["fn"]},
            "z": {"deps": ["y"], "fn": valid["fn"]},
        })
        for index, graph in enumerate(invalid_graphs):
            with self.subTest(case=index):
                with self.assertRaises(ValueError):
                    run_graph(graph)
                self.assertEqual(calls, [])

    def test_sorted_results_exact_values_and_no_input_mutation(self):
        value = object()
        tasks = {
            "z": {"deps": ["c", "a"], "fn": lambda: value, "extra": 10},
            "c": {"deps": ["a"], "fn": lambda: None},
            "a": {"deps": [], "fn": lambda: False},
        }
        original = {key: {**task, "deps": list(task["deps"])}
                    for key, task in tasks.items()}
        dependency_lists = {key: task["deps"] for key, task in tasks.items()}
        result = run_graph(MappingProxyType(tasks))
        self.assertEqual(list(result), ["a", "c", "z"])
        self.assertEqual(result, {
            "a": {"status": "completed", "value": False},
            "c": {"status": "completed", "value": None},
            "z": {"status": "completed", "value": value},
        })
        self.assertIs(result["z"]["value"], value)
        self.assertEqual(tasks, original)
        for key in tasks:
            self.assertIs(tasks[key]["deps"], dependency_lists[key])

    def test_all_parents_finish_before_dependent_runs_once(self):
        calls = []
        lock = threading.Lock()

        def record(task_id):
            with lock:
                calls.append(task_id)
            return task_id

        tasks = {
            "d": {"deps": ["c", "a"], "fn": lambda: record("d")},
            "c": {"deps": ["a", "b"], "fn": lambda: record("c")},
            "b": {"deps": [], "fn": lambda: record("b")},
            "a": {"deps": [], "fn": lambda: record("a")},
        }
        result = run_graph(tasks)
        self.assertCountEqual(calls, tasks)
        for task_id, task in tasks.items():
            self.assertEqual(result[task_id],
                             {"status": "completed", "value": task_id})
            for dependency in task["deps"]:
                self.assertLess(calls.index(dependency), calls.index(task_id))

    def test_failure_skips_transitive_joins_and_keeps_unrelated_work(self):
        def fail():
            raise RuntimeError("failure message")

        def forbidden():
            self.fail("a blocked task was executed")

        tasks = {
            "bad": {"deps": [], "fn": fail},
            "other_bad": {"deps": [], "fn": fail},
            "good": {"deps": [], "fn": lambda: 7},
            "good_child": {"deps": ["good"], "fn": lambda: 8},
            "left": {"deps": ["bad"], "fn": forbidden},
            "join": {"deps": ["bad", "good", "other_bad"], "fn": forbidden},
            "leaf": {"deps": ["left", "join"], "fn": forbidden},
        }
        self.assertEqual(run_graph(tasks), {
            "bad": {"status": "failed", "error": "failure message"},
            "good": {"status": "completed", "value": 7},
            "good_child": {"status": "completed", "value": 8},
            "join": {"status": "skipped"},
            "leaf": {"status": "skipped"},
            "left": {"status": "skipped"},
            "other_bad": {"status": "failed", "error": "failure message"},
        })

    def test_independent_tasks_overlap_without_exceeding_worker_limit(self):
        for workers in (1, 2, 3):
            with self.subTest(workers=workers):
                barrier = threading.Barrier(workers)
                lock = threading.Lock()
                active = 0
                peak = 0

                def work():
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

                tasks = {str(index): {"deps": [], "fn": work}
                         for index in range(workers * 4)}
                result = run_graph(tasks, max_workers=workers)
                self.assertEqual(peak, workers)
                self.assertEqual(active, 0)
                self.assertTrue(all(outcome == {"status": "completed", "value": "done"}
                                    for outcome in result.values()))

    def test_newly_ready_task_does_not_wait_for_unrelated_slow_task(self):
        slow_started = threading.Event()
        dependent_started = threading.Event()

        def slow():
            slow_started.set()
            if not dependent_started.wait(timeout=5):
                raise RuntimeError("dependent did not start while slow was running")
            return "slow finished"

        def fast():
            if not slow_started.wait(timeout=5):
                raise RuntimeError("independent roots did not overlap")
            return "fast finished"

        def dependent():
            dependent_started.set()
            return "dependent finished"

        tasks = {
            "a_slow": {"deps": [], "fn": slow},
            "b_fast": {"deps": [], "fn": fast},
            "c_dependent": {"deps": ["b_fast"], "fn": dependent},
        }
        self.assertEqual(run_graph(tasks), {
            "a_slow": {"status": "completed", "value": "slow finished"},
            "b_fast": {"status": "completed", "value": "fast finished"},
            "c_dependent": {"status": "completed", "value": "dependent finished"},
        })

    def test_return_waits_for_started_work_after_failure(self):
        slow_started = threading.Event()
        failed = threading.Event()
        release_slow = threading.Event()
        returned = threading.Event()
        results = []
        errors = []

        def slow():
            slow_started.set()
            if not release_slow.wait(timeout=5):
                raise RuntimeError("slow task was not released")
            return "finished"

        def fail():
            if not slow_started.wait(timeout=5):
                raise RuntimeError("slow task did not start")
            failed.set()
            raise ValueError("boom")

        tasks = {
            "a_slow": {"deps": [], "fn": slow},
            "b_failure": {"deps": [], "fn": fail},
            "c_blocked": {"deps": ["b_failure"], "fn": lambda: None},
        }

        def execute():
            try:
                results.append(run_graph(tasks))
            except Exception as error:
                errors.append(error)
            finally:
                returned.set()

        runner = threading.Thread(target=execute, daemon=True)
        runner.start()
        try:
            self.assertTrue(failed.wait(timeout=5))
            self.assertFalse(returned.wait(timeout=0.1))
        finally:
            release_slow.set()
            runner.join(timeout=5)
        self.assertFalse(runner.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results, [{
            "a_slow": {"status": "completed", "value": "finished"},
            "b_failure": {"status": "failed", "error": "boom"},
            "c_blocked": {"status": "skipped"},
        }])

    def test_chain_of_1500_tasks(self):
        calls = []
        tasks = {
            str(index): {
                "deps": [str(index - 1)] if index else [],
                "fn": lambda index=index: calls.append(index) or index,
            }
            for index in range(1500)
        }
        result = run_graph(tasks)
        self.assertEqual(calls, list(range(1500)))
        self.assertEqual(list(result), sorted(tasks))
        self.assertEqual(result["1499"], {"status": "completed", "value": 1499})

    def test_deep_failure_propagation_is_iterative(self):
        def fail():
            raise ValueError("root failed")

        def forbidden():
            self.fail("a descendant of a failed root ran")

        tasks = {
            str(index): {
                "deps": [str(index - 1)] if index else [],
                "fn": forbidden if index else fail,
            }
            for index in range(1500)
        }
        result = run_graph(tasks)
        self.assertEqual(result["0"], {"status": "failed", "error": "root failed"})
        self.assertTrue(all(result[str(index)] == {"status": "skipped"}
                            for index in range(1, 1500)))

    def test_deep_cycle_is_rejected_before_execution(self):
        calls = []
        tasks = {
            str(index): {
                "deps": [str((index - 1) % 1500)],
                "fn": lambda: calls.append("called"),
            }
            for index in range(1500)
        }
        with self.assertRaises(ValueError):
            run_graph(tasks)
        self.assertEqual(calls, [])
