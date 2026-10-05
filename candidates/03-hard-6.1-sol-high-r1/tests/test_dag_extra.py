import threading
import unittest

from dag import run_graph


class GraphTests(unittest.TestCase):
    def test_validation_runs_no_functions(self):
        called = []

        def fn():
            called.append(True)

        malformed = [
            None, [], {"": {"deps": [], "fn": fn}},
            {1: {"deps": [], "fn": fn}},
            {"bad": None}, {"bad": {}},
            {"bad": {"deps": [], "fn": None}},
            {"bad": {"deps": (), "fn": fn}},
            {"bad": {"deps": [[], "ok"], "fn": fn}},
            {"bad": {"deps": [""], "fn": fn}},
            {"bad": {"deps": [1], "fn": fn}},
            {"bad": {"deps": ["ok", "ok"], "fn": fn}},
            {"bad": {"deps": ["missing"], "fn": fn}},
            {"bad": {"deps": ["bad"], "fn": fn}},
            {"bad": {"deps": ["other"], "fn": fn},
             "other": {"deps": ["bad"], "fn": fn}},
        ]
        for tasks in malformed:
            if isinstance(tasks, dict):
                tasks = {"ok": {"deps": [], "fn": fn}, **tasks}
            with self.subTest(tasks=tasks):
                with self.assertRaises(ValueError):
                    run_graph(tasks)
        self.assertEqual(called, [])

    def test_worker_validation_and_empty_graph(self):
        for workers in (True, False, 0, -1, 1.0, "2", None):
            with self.subTest(workers=workers):
                with self.assertRaises(ValueError):
                    run_graph({}, workers)
        self.assertEqual(run_graph({}), {})

    def test_failure_skips_shared_transitive_descendants(self):
        called = []

        def fail():
            raise RuntimeError("broken")

        def record(task_id):
            def fn():
                called.append(task_id)
                return task_id
            return fn

        tasks = {
            "z": {"deps": [], "fn": record("z")},
            "join": {"deps": ["bad", "z"], "fn": record("join")},
            "leaf": {"deps": ["join", "bad"], "fn": record("leaf")},
            "bad": {"deps": [], "fn": fail},
            "independent": {"deps": ["z"], "fn": record("independent")},
        }
        snapshot = {key: {"deps": task["deps"][:], "fn": task["fn"]}
                    for key, task in tasks.items()}
        result = run_graph(tasks)
        self.assertEqual(list(result), sorted(tasks))
        self.assertEqual(result, {
            "bad": {"status": "failed", "error": "broken"},
            "independent": {"status": "completed", "value": "independent"},
            "join": {"status": "skipped"},
            "leaf": {"status": "skipped"},
            "z": {"status": "completed", "value": "z"},
        })
        self.assertCountEqual(called, ["z", "independent"])
        self.assertEqual(tasks, snapshot)

    def test_newly_ready_child_runs_while_unrelated_task_waits(self):
        slow_started = threading.Event()
        child_finished = threading.Event()
        slow_finished = threading.Event()

        def slow():
            slow_started.set()
            if not child_finished.wait(3):
                raise RuntimeError("newly ready child was not scheduled")
            slow_finished.set()
            return "slow"

        def fast():
            if not slow_started.wait(3):
                raise RuntimeError("independent roots did not run concurrently")
            return "fast"

        def child():
            child_finished.set()
            return "child"

        result = run_graph({
            "a_slow": {"deps": [], "fn": slow},
            "b_fast": {"deps": [], "fn": fast},
            "c_child": {"deps": ["b_fast"], "fn": child},
        })
        self.assertEqual(result["a_slow"], {"status": "completed", "value": "slow"})
        self.assertEqual(result["c_child"], {"status": "completed", "value": "child"})
        self.assertTrue(slow_finished.is_set())

    def test_worker_bound_and_each_function_called_once(self):
        barrier = threading.Barrier(2, timeout=3)
        lock = threading.Lock()
        active = 0
        peak = 0
        calls = []

        def fn():
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                calls.append(True)
            try:
                barrier.wait()
                barrier.wait()
            finally:
                with lock:
                    active -= 1

        result = run_graph({str(i): {"deps": [], "fn": fn} for i in range(6)}, 2)
        self.assertEqual(peak, 2)
        self.assertEqual(len(calls), 6)
        self.assertTrue(all(task == {"status": "completed", "value": None}
                            for task in result.values()))

    def test_join_waits_for_all_successful_parents(self):
        first_started = threading.Event()
        second_finished = threading.Event()
        first_finished = threading.Event()

        def first():
            first_started.set()
            self.assertTrue(second_finished.wait(3))
            first_finished.set()

        def second():
            self.assertTrue(first_started.wait(3))
            second_finished.set()

        def join():
            self.assertTrue(first_finished.is_set())
            self.assertTrue(second_finished.is_set())
            return 42

        result = run_graph({
            "a": {"deps": [], "fn": first},
            "b": {"deps": [], "fn": second},
            "join": {"deps": ["a", "b"], "fn": join},
        })
        self.assertEqual(result["join"], {"status": "completed", "value": 42})

    def test_long_chains_without_recursion(self):
        tasks = {str(i): {"deps": [str(i - 1)] if i else [],
                          "fn": lambda i=i: i} for i in range(1500)}
        result = run_graph(tasks, 1)
        self.assertEqual(len(result), 1500)
        self.assertEqual(result["1499"], {"status": "completed", "value": 1499})

        def fail():
            raise ValueError("root failed")

        tasks["0"]["fn"] = fail
        result = run_graph(tasks)
        self.assertEqual(result["0"], {"status": "failed", "error": "root failed"})
        self.assertTrue(all(result[str(i)] == {"status": "skipped"}
                            for i in range(1, 1500)))


if __name__ == "__main__":
    unittest.main()
