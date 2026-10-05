import copy
import threading
import unittest
from collections import Counter

from dag import run_graph


class ValidationTests(unittest.TestCase):
    def test_malformed_graphs_are_rejected_before_any_call(self):
        calls = []
        fn = lambda: calls.append("called")
        invalid_tasks = [
            {"": {"deps": [], "fn": fn}},
            {1: {"deps": [], "fn": fn}},
            {"bad": None},
            {"bad": []},
            {"bad": {}},
            {"bad": {"deps": []}},
            {"bad": {"fn": fn}},
            {"bad": {"deps": [], "fn": None}},
            {"bad": {"deps": [], "fn": 42}},
            {"bad": {"deps": None, "fn": fn}},
            {"bad": {"deps": "a_root", "fn": fn}},
            {"bad": {"deps": ("a_root",), "fn": fn}},
            {"bad": {"deps": {"a_root"}, "fn": fn}},
            {"bad": {"deps": [""], "fn": fn}},
            {"bad": {"deps": [1], "fn": fn}},
            {"bad": {"deps": [None], "fn": fn}},
            {"bad": {"deps": [[]], "fn": fn}},
            {"bad": {"deps": [{}], "fn": fn}},
            {"bad": {"deps": ["missing"], "fn": fn}},
            {"bad": {"deps": ["a_root", "a_root"], "fn": fn}},
            {"bad": {"deps": ["bad"], "fn": fn}},
            {"bad": {"deps": ["cycle"], "fn": fn},
             "cycle": {"deps": ["bad"], "fn": fn}},
        ]
        for invalid in invalid_tasks:
            with self.subTest(invalid=invalid):
                tasks = {"a_root": {"deps": [], "fn": fn}, **invalid}
                with self.assertRaises(ValueError):
                    run_graph(tasks)
                self.assertEqual(calls, [])

    def test_invalid_outer_container(self):
        for tasks in (None, [], (), "tasks", 1):
            with self.subTest(tasks=tasks):
                with self.assertRaises(ValueError):
                    run_graph(tasks)

    def test_invalid_worker_counts_including_empty_graph(self):
        calls = []
        tasks = {"a": {"deps": [], "fn": lambda: calls.append("a")}}
        for workers in (True, False, 0, -1, 1.0, 2.5, "2", None, [], {}):
            for graph in (tasks, {}):
                with self.subTest(workers=workers, empty=not graph):
                    with self.assertRaises(ValueError):
                        run_graph(graph, max_workers=workers)
                    self.assertEqual(calls, [])

    def test_empty_graph(self):
        self.assertEqual(run_graph({}), {})

    def test_long_cycle_is_rejected_without_running_functions(self):
        calls = []
        tasks = {
            str(i): {"deps": [str((i + 1) % 1500)],
                     "fn": lambda: calls.append("called")}
            for i in range(1500)
        }
        with self.assertRaises(ValueError):
            run_graph(tasks)
        self.assertEqual(calls, [])


class ExecutionTests(unittest.TestCase):
    def test_sorted_results_exact_shapes_and_no_input_mutation(self):
        value = object()

        def fail():
            raise ValueError("broken")

        deps = ["z", "a"]
        tasks = {
            "z": {"deps": [], "fn": lambda: value},
            "d": {"deps": deps, "fn": lambda: None},
            "c": {"deps": ["b"], "fn": lambda: "unreachable"},
            "b": {"deps": [], "fn": fail},
            "a": {"deps": [], "fn": lambda: None},
        }
        snapshot = copy.deepcopy(tasks)
        result = run_graph(tasks)
        self.assertEqual(list(result), ["a", "b", "c", "d", "z"])
        self.assertEqual(result, {
            "a": {"status": "completed", "value": None},
            "b": {"status": "failed", "error": "broken"},
            "c": {"status": "skipped"},
            "d": {"status": "completed", "value": None},
            "z": {"status": "completed", "value": value},
        })
        self.assertIs(result["z"]["value"], value)
        self.assertEqual(tasks, snapshot)
        self.assertEqual(list(tasks), ["z", "d", "c", "b", "a"])
        self.assertIs(tasks["d"]["deps"], deps)

    def test_failures_skip_overlapping_descendants_with_multiple_parents(self):
        calls = Counter()
        lock = threading.Lock()

        def function(task_id, fails=False):
            def invoke():
                with lock:
                    calls[task_id] += 1
                if fails:
                    raise RuntimeError(task_id)
                return task_id
            return invoke

        deps = {
            "failure1": [],
            "failure2": [],
            "success": [],
            "left": ["failure1"],
            "right": ["failure2", "success"],
            "join": ["left", "right", "success"],
            "leaf": ["join", "success"],
            "unrelated": ["success"],
        }
        tasks = {
            task_id: {"deps": parents,
                      "fn": function(task_id, task_id.startswith("failure"))}
            for task_id, parents in deps.items()
        }
        result = run_graph(tasks, max_workers=3)
        self.assertEqual(calls, Counter({
            "failure1": 1, "failure2": 1, "success": 1, "unrelated": 1,
        }))
        for task_id in ("failure1", "failure2"):
            self.assertEqual(result[task_id], {"status": "failed", "error": task_id})
        for task_id in ("left", "right", "join", "leaf"):
            self.assertEqual(result[task_id], {"status": "skipped"})
        for task_id in ("success", "unrelated"):
            self.assertEqual(result[task_id], {"status": "completed", "value": task_id})

    def test_successful_diamond_runs_each_function_once(self):
        calls = Counter()
        lock = threading.Lock()
        deps = {"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]}

        def function(task_id):
            def invoke():
                with lock:
                    self.assertTrue(all(calls[parent] == 1 for parent in deps[task_id]))
                    calls[task_id] += 1
                return task_id
            return invoke

        tasks = {task_id: {"deps": parents, "fn": function(task_id)}
                 for task_id, parents in deps.items()}
        self.assertEqual(run_graph(tasks), {
            task_id: {"status": "completed", "value": task_id}
            for task_id in deps
        })
        self.assertEqual(calls, Counter({task_id: 1 for task_id in deps}))

    def test_long_successful_chain(self):
        calls = []
        tasks = {
            f"task_{i:04d}": {
                "deps": [f"task_{i - 1:04d}"] if i else [],
                "fn": lambda i=i: calls.append(i) or i,
            }
            for i in range(1500)
        }
        result = run_graph(tasks)
        self.assertEqual(calls, list(range(1500)))
        self.assertEqual(result, {
            f"task_{i:04d}": {"status": "completed", "value": i}
            for i in range(1500)
        })

    def test_failure_skips_long_chain_without_recursion(self):
        calls = []

        def fail():
            raise RuntimeError("root failure")

        tasks = {
            f"task_{i:04d}": {
                "deps": [f"task_{i - 1:04d}"] if i else [],
                "fn": (lambda: calls.append("unexpected")) if i else fail,
            }
            for i in range(1500)
        }
        result = run_graph(tasks)
        self.assertEqual(calls, [])
        self.assertEqual(len(result), 1500)
        self.assertEqual(result["task_0000"], {"status": "failed", "error": "root failure"})
        self.assertTrue(all(result[f"task_{i:04d}"] == {"status": "skipped"}
                            for i in range(1, 1500)))


class ConcurrencyTests(unittest.TestCase):
    def start_graph(self, tasks, max_workers=2):
        outcome = {}
        finished = threading.Event()

        def run():
            try:
                outcome["result"] = run_graph(tasks, max_workers=max_workers)
            except Exception as exc:
                outcome["error"] = exc
            finally:
                finished.set()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread, finished, outcome

    def result(self, thread, outcome):
        self.assertFalse(thread.is_alive(), "run_graph did not finish")
        if "error" in outcome:
            raise outcome["error"]
        return outcome["result"]

    def test_independent_work_uses_bounded_concurrency(self):
        release = threading.Event()
        saturated = threading.Event()
        exceeded = threading.Event()
        lock = threading.Lock()
        active = 0
        peak = 0
        calls = 0

        def work():
            nonlocal active, peak, calls
            with lock:
                active += 1
                calls += 1
                peak = max(peak, active)
                if active == 3:
                    saturated.set()
                if active > 3:
                    exceeded.set()
            release.wait()
            with lock:
                active -= 1
            return "done"

        tasks = {str(i): {"deps": [], "fn": work} for i in range(8)}
        thread, finished, outcome = self.start_graph(tasks, max_workers=3)
        try:
            self.assertTrue(saturated.wait(5), "independent tasks did not run concurrently")
            self.assertFalse(exceeded.wait(0.1), "max_workers was exceeded")
            self.assertFalse(finished.is_set())
        finally:
            release.set()
            thread.join(5)
        result = self.result(thread, outcome)
        self.assertEqual(peak, 3)
        self.assertEqual(calls, 8)
        self.assertEqual(result, {
            str(i): {"status": "completed", "value": "done"} for i in range(8)
        })

    def test_ready_dependent_starts_while_unrelated_work_is_running(self):
        slow_started = threading.Event()
        release_slow = threading.Event()
        child_started = threading.Event()

        def slow():
            slow_started.set()
            release_slow.wait()
            return "slow"

        def fast():
            if not slow_started.wait(5):
                raise RuntimeError("slow task did not start")
            return "fast"

        def child():
            child_started.set()
            return "child"

        tasks = {
            "a_slow": {"deps": [], "fn": slow},
            "z_fast": {"deps": [], "fn": fast},
            "child": {"deps": ["z_fast"], "fn": child},
        }
        thread, finished, outcome = self.start_graph(tasks)
        try:
            self.assertTrue(slow_started.wait(5))
            self.assertTrue(child_started.wait(5), "child waited for unrelated slow work")
            self.assertFalse(finished.is_set(), "returned while slow work was still running")
        finally:
            release_slow.set()
            thread.join(5)
        self.assertEqual(self.result(thread, outcome), {
            "a_slow": {"status": "completed", "value": "slow"},
            "child": {"status": "completed", "value": "child"},
            "z_fast": {"status": "completed", "value": "fast"},
        })

    def test_child_waits_for_all_parents(self):
        fast_finished = threading.Event()
        slow_started = threading.Event()
        release_slow = threading.Event()
        child_started = threading.Event()

        def fast():
            fast_finished.set()

        def slow():
            slow_started.set()
            release_slow.wait()

        def child():
            child_started.set()

        tasks = {
            "fast": {"deps": [], "fn": fast},
            "slow": {"deps": [], "fn": slow},
            "child": {"deps": ["fast", "slow"], "fn": child},
        }
        thread, _, outcome = self.start_graph(tasks)
        try:
            self.assertTrue(fast_finished.wait(5))
            self.assertTrue(slow_started.wait(5))
            self.assertFalse(child_started.wait(0.1), "child started before all parents finished")
        finally:
            release_slow.set()
            thread.join(5)
        self.assertTrue(child_started.is_set())
        self.assertEqual(self.result(thread, outcome), {
            task_id: {"status": "completed", "value": None}
            for task_id in ("child", "fast", "slow")
        })


if __name__ == "__main__":
    unittest.main()
