import copy
import threading
import unittest

from dag import run_graph


class ExecutorTests(unittest.TestCase):
    def test_invalid_graphs_never_start_work(self):
        calls = []
        good = {"deps": [], "fn": lambda: calls.append("called")}
        invalid_tasks = [
            None, [], {"": good}, {1: good},
            {"a": None}, {"a": {}},
            {"a": {"deps": (), "fn": lambda: None}},
            {"a": {"deps": [], "fn": 1}},
            {"a": {"deps": ["missing"], "fn": lambda: None}},
            {"a": {"deps": ["a"], "fn": lambda: None}},
            {"a": good, "b": {"deps": ["a", "a"], "fn": lambda: None}},
            {"a": good, "b": {"deps": [[]], "fn": lambda: None}},
            {"a": good, "b": {"deps": ["c"], "fn": lambda: None},
             "c": {"deps": ["b"], "fn": lambda: None}},
        ]
        for tasks in invalid_tasks:
            with self.subTest(tasks=tasks), self.assertRaises(ValueError):
                run_graph(tasks)
        self.assertEqual(calls, [])
        for workers in (True, False, 0, -1, 1.5, "2", None):
            with self.subTest(workers=workers), self.assertRaises(ValueError):
                run_graph({}, workers)
        self.assertEqual(run_graph({}), {})

    def test_failure_skips_shared_descendants_and_preserves_input(self):
        calls = []

        def fail():
            calls.append("failed")
            raise RuntimeError("problem")

        def record(task_id):
            return lambda: calls.append(task_id) or task_id

        tasks = {
            "z": {"deps": [], "fn": record("z")},
            "a": {"deps": [], "fn": fail},
            "b": {"deps": ["a", "z"], "fn": record("b")},
            "c": {"deps": ["b", "z"], "fn": record("c")},
            "d": {"deps": ["z"], "fn": record("d")},
        }
        before = copy.deepcopy(tasks)
        result = run_graph(tasks)
        self.assertEqual(tasks, before)
        self.assertEqual(list(result), ["a", "b", "c", "d", "z"])
        self.assertEqual(result, {
            "a": {"status": "failed", "error": "problem"},
            "b": {"status": "skipped"}, "c": {"status": "skipped"},
            "d": {"status": "completed", "value": "d"},
            "z": {"status": "completed", "value": "z"},
        })
        self.assertCountEqual(calls, ["failed", "z", "d"])

    def test_newly_ready_work_does_not_wait_for_slow_root(self):
        slow_started = threading.Event()
        child_started = threading.Event()
        slow_finished = threading.Event()

        def slow():
            slow_started.set()
            observed = child_started.wait(3)
            slow_finished.set()
            return observed

        def fast():
            return slow_started.wait(3)

        def child():
            child_started.set()
            return True

        result = run_graph({
            "a_slow": {"deps": [], "fn": slow},
            "b_fast": {"deps": [], "fn": fast},
            "c_child": {"deps": ["b_fast"], "fn": child},
        }, max_workers=2)
        self.assertTrue(slow_finished.is_set())
        self.assertTrue(all(entry["value"] for entry in result.values()))

    def test_worker_limit_and_all_parents(self):
        barrier = threading.Barrier(3, timeout=3)
        lock = threading.Lock()
        active = 0
        peak = 0
        completed = set()

        def root(task_id):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            barrier.wait()
            with lock:
                active -= 1
                completed.add(task_id)
            return task_id

        def child():
            return completed == {"a", "b", "c", "d", "e", "f"}

        tasks = {key: {"deps": [], "fn": lambda key=key: root(key)}
                 for key in "abcdef"}
        tasks["g"] = {"deps": list("abcdef"), "fn": child}
        result = run_graph(tasks, 3)
        self.assertEqual(peak, 3)
        self.assertEqual(result["g"], {"status": "completed", "value": True})

    def test_long_chain_and_long_failure_propagation(self):
        tasks = {str(i): {"deps": [str(i - 1)] if i else [],
                          "fn": lambda i=i: i} for i in range(1500)}
        result = run_graph(tasks)
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
