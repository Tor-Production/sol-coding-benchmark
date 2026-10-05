import threading
import unittest
from collections import Counter

from dag import run_graph


TIMEOUT = 5


class SchedulerTests(unittest.TestCase):
    def test_empty_graph_and_invalid_worker_counts(self):
        self.assertEqual(run_graph({}), {})
        called = []
        tasks = {"a": {"deps": [], "fn": lambda: called.append("a")}}
        for workers in (0, -1, True, False, 1.5, "2", None, [], {}):
            with self.subTest(workers=workers):
                with self.assertRaises(ValueError):
                    run_graph(tasks, workers)
                with self.assertRaises(ValueError):
                    run_graph({}, workers)
        self.assertEqual(called, [])

    def test_validation_precedes_every_function(self):
        called = []
        fn = lambda: called.append("called")
        malformed = [
            None,
            [],
            "task",
            {},
            {"deps": []},
            {"fn": fn},
            {"deps": None, "fn": fn},
            {"deps": (), "fn": fn},
            {"deps": "a_valid", "fn": fn},
            {"deps": [], "fn": None},
            {"deps": ["a_valid", "a_valid"], "fn": fn},
            {"deps": ["missing"], "fn": fn},
            {"deps": ["z_bad"], "fn": fn},
            {"deps": [None], "fn": fn},
            {"deps": [[]], "fn": fn},
            {"deps": [""], "fn": fn},
            {"deps": [1], "fn": fn},
        ]
        for task in malformed:
            with self.subTest(task=task):
                with self.assertRaises(ValueError):
                    run_graph({"a_valid": {"deps": [], "fn": fn}, "z_bad": task})
                self.assertEqual(called, [])

        for bad_id in ("", 0, None, True, ("id",)):
            with self.subTest(task_id=bad_id):
                with self.assertRaises(ValueError):
                    run_graph({"a_valid": {"deps": [], "fn": fn},
                               bad_id: {"deps": [], "fn": fn}})
                self.assertEqual(called, [])

        for invalid_graph in (None, [], [("a", {"deps": [], "fn": fn})], "tasks"):
            with self.subTest(graph=invalid_graph):
                with self.assertRaises(ValueError):
                    run_graph(invalid_graph)
        with self.assertRaises(ValueError):
            run_graph({"a_valid": {"deps": [], "fn": fn},
                       "b": {"deps": ["c"], "fn": fn},
                       "c": {"deps": ["b"], "fn": fn}})
        self.assertEqual(called, [])

    def test_sorted_exact_results_and_input_preservation(self):
        value = object()
        tasks = {
            "z": {"deps": ["b", "a"], "fn": lambda: value, "extra": "kept"},
            "b": {"deps": ["a"], "fn": lambda: [1, 2]},
            "a": {"deps": [], "fn": lambda: None},
        }
        original = {name: {**task, "deps": list(task["deps"])}
                    for name, task in tasks.items()}
        dep_lists = {name: task["deps"] for name, task in tasks.items()}
        results = run_graph(tasks, max_workers=1)
        self.assertEqual(list(results), ["a", "b", "z"])
        self.assertEqual(results, {
            "a": {"status": "completed", "value": None},
            "b": {"status": "completed", "value": [1, 2]},
            "z": {"status": "completed", "value": value},
        })
        self.assertIs(results["z"]["value"], value)
        self.assertEqual(tasks, original)
        for name in tasks:
            self.assertIs(tasks[name]["deps"], dep_lists[name])

    def test_independent_tasks_overlap_with_bounded_workers(self):
        barrier = threading.Barrier(2, timeout=TIMEOUT)
        lock = threading.Lock()
        active = 0
        peak = 0
        calls = Counter()

        def work(name):
            nonlocal active, peak
            with lock:
                calls[name] += 1
                active += 1
                peak = max(peak, active)
            try:
                barrier.wait()
                return name
            finally:
                with lock:
                    active -= 1

        tasks = {str(i): {"deps": [], "fn": lambda name=str(i): work(name)}
                 for i in range(6)}
        results = run_graph(tasks, max_workers=2)
        self.assertEqual(peak, 2)
        self.assertEqual(calls, Counter({name: 1 for name in tasks}))
        self.assertEqual(results, {name: {"status": "completed", "value": name}
                                   for name in sorted(tasks)})

    def test_newly_ready_task_starts_while_unrelated_task_is_running(self):
        slow_started = threading.Event()
        dependent_started = threading.Event()

        def slow():
            slow_started.set()
            self.assertTrue(dependent_started.wait(TIMEOUT),
                            "dependent did not start while a worker was free")
            return "slow"

        def fast():
            self.assertTrue(slow_started.wait(TIMEOUT))
            return "fast"

        def dependent():
            dependent_started.set()
            return "dependent"

        tasks = {
            "a_slow": {"deps": [], "fn": slow},
            "b_fast": {"deps": [], "fn": fast},
            "c_dependent": {"deps": ["b_fast"], "fn": dependent},
        }
        self.assertEqual(run_graph(tasks), {
            "a_slow": {"status": "completed", "value": "slow"},
            "b_fast": {"status": "completed", "value": "fast"},
            "c_dependent": {"status": "completed", "value": "dependent"},
        })

    def test_join_waits_for_all_successful_parents(self):
        release_slow = threading.Event()
        slow_finished = threading.Event()
        fast_finished = threading.Event()
        joined = threading.Event()

        def slow():
            self.assertTrue(release_slow.wait(TIMEOUT))
            slow_finished.set()
            return 1

        def fast():
            fast_finished.set()
            return 2

        def release():
            try:
                self.assertFalse(joined.is_set())
                return 3
            finally:
                release_slow.set()

        def join():
            joined.set()
            self.assertTrue(slow_finished.is_set())
            self.assertTrue(fast_finished.is_set())
            return 4

        tasks = {
            "a_slow": {"deps": [], "fn": slow},
            "b_fast": {"deps": [], "fn": fast},
            "c_release": {"deps": ["b_fast"], "fn": release},
            "d_join": {"deps": ["a_slow", "b_fast"], "fn": join},
        }
        results = run_graph(tasks)
        self.assertEqual(results, {
            name: {"status": "completed", "value": value}
            for name, value in zip(sorted(tasks), (1, 2, 3, 4))
        })

    def test_failures_skip_transitive_descendants_and_preserve_other_work(self):
        calls = Counter()
        lock = threading.Lock()

        def work(name, fail=False):
            with lock:
                calls[name] += 1
            if fail:
                raise RuntimeError("failure: " + name)
            return name

        deps = {
            "a_fail": [],
            "b_fail": [],
            "c_good": [],
            "d_skip": ["a_fail", "c_good"],
            "e_skip": ["b_fail", "c_good"],
            "f_skip": ["d_skip", "e_skip", "c_good"],
            "g_skip": ["f_skip"],
            "x_good": ["c_good"],
            "y_good": ["x_good", "c_good"],
        }
        tasks = {name: {"deps": parents,
                        "fn": lambda name=name: work(name, name.endswith("fail"))}
                 for name, parents in deps.items()}
        results = run_graph(tasks)
        expected = {}
        for name in sorted(tasks):
            if name.endswith("fail"):
                expected[name] = {"status": "failed", "error": "failure: " + name}
            elif name.endswith("skip"):
                expected[name] = {"status": "skipped"}
            else:
                expected[name] = {"status": "completed", "value": name}
        self.assertEqual(results, expected)
        self.assertEqual(calls, Counter({name: 1 for name in tasks
                                        if not name.endswith("skip")}))

    def test_returns_only_after_started_work_finishes(self):
        slow_started = threading.Event()
        slow_finished = threading.Event()
        failure_started = threading.Event()
        release_slow = threading.Event()
        returned = threading.Event()
        results = []
        errors = []

        def fail():
            self.assertTrue(slow_started.wait(TIMEOUT))
            failure_started.set()
            raise ValueError("broken")

        def slow():
            slow_started.set()
            self.assertTrue(release_slow.wait(TIMEOUT))
            slow_finished.set()
            return "finished"

        tasks = {
            "a_fail": {"deps": [], "fn": fail},
            "b_slow": {"deps": [], "fn": slow},
            "c_skip": {"deps": ["a_fail", "b_slow"],
                       "fn": lambda: self.fail("blocked function was called")},
        }

        def execute():
            try:
                results.append(run_graph(tasks))
            except Exception as exc:
                errors.append(exc)
            finally:
                returned.set()

        runner = threading.Thread(target=execute, daemon=True)
        runner.start()
        try:
            self.assertTrue(slow_started.wait(TIMEOUT))
            self.assertTrue(failure_started.wait(TIMEOUT))
            self.assertFalse(returned.wait(0.1))
        finally:
            release_slow.set()
            runner.join(TIMEOUT)
        self.assertFalse(runner.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(slow_finished.is_set())
        self.assertEqual(results, [{
            "a_fail": {"status": "failed", "error": "broken"},
            "b_slow": {"status": "completed", "value": "finished"},
            "c_skip": {"status": "skipped"},
        }])

    def test_long_chain_completes_and_skips_without_recursion(self):
        count = 1500
        calls = []

        def work(index):
            calls.append(index)
            return index

        tasks = {f"node_{i:04d}": {
            "deps": [] if i == 0 else [f"node_{i - 1:04d}"],
            "fn": lambda i=i: work(i),
        } for i in range(count)}
        results = run_graph(tasks)
        self.assertEqual(calls, list(range(count)))
        self.assertEqual(results, {f"node_{i:04d}": {
            "status": "completed", "value": i,
        } for i in range(count)})

        def fail():
            raise RuntimeError("root failure")

        calls.clear()
        tasks["node_0000"]["fn"] = fail
        results = run_graph(tasks)
        self.assertEqual(calls, [])
        self.assertEqual(results["node_0000"], {
            "status": "failed", "error": "root failure",
        })
        self.assertEqual(list(results), sorted(tasks))
        self.assertTrue(all(result == {"status": "skipped"}
                            for name, result in results.items() if name != "node_0000"))


if __name__ == "__main__":
    unittest.main()
