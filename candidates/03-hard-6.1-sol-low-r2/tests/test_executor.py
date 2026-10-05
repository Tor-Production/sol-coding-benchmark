import threading
import unittest

from dag import run_graph


class ExecutorTests(unittest.TestCase):
    def test_validation_before_execution(self):
        called = []
        valid = {"deps": [], "fn": lambda: called.append(True)}
        invalid = [
            {"deps": ["missing"], "fn": lambda: None},
            {"deps": ["a", "a"], "fn": lambda: None},
            {"deps": ["b"], "fn": lambda: None},
            {"deps": [None], "fn": lambda: None},
            {"deps": (), "fn": lambda: None},
            {"deps": [], "fn": 1},
            {}, None,
        ]
        for task in invalid:
            with self.subTest(task=task), self.assertRaises(ValueError):
                run_graph({"a": valid, "b": task})
        for workers in [True, False, 0, -1, 1.5, "2", None]:
            with self.subTest(workers=workers), self.assertRaises(ValueError):
                run_graph({}, workers)
        for key in ["", 1, None]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                run_graph({key: valid})
        self.assertEqual(called, [])
        self.assertEqual(run_graph({}), {})

    def test_failure_with_multiple_parents_and_sorted_results(self):
        called = []

        def fail():
            raise RuntimeError("broken")

        tasks = {
            "z": {"deps": ["b"], "fn": lambda: called.append("z")},
            "b": {"deps": ["a", "c"], "fn": lambda: called.append("b")},
            "c": {"deps": [], "fn": lambda: 3},
            "a": {"deps": [], "fn": fail},
        }
        deps = tasks["b"]["deps"][:]
        result = run_graph(tasks)
        self.assertEqual(list(result), ["a", "b", "c", "z"])
        self.assertEqual(result, {
            "a": {"status": "failed", "error": "broken"},
            "b": {"status": "skipped"},
            "c": {"status": "completed", "value": 3},
            "z": {"status": "skipped"},
        })
        self.assertEqual(called, [])
        self.assertEqual(tasks["b"]["deps"], deps)

    def test_dependent_starts_while_unrelated_task_is_running(self):
        slow_started = threading.Event()
        dependent_started = threading.Event()

        def slow():
            slow_started.set()
            self.assertTrue(dependent_started.wait(5))

        def fast():
            self.assertTrue(slow_started.wait(5))

        def dependent():
            dependent_started.set()

        result = run_graph({
            "slow": {"deps": [], "fn": slow},
            "fast": {"deps": [], "fn": fast},
            "dependent": {"deps": ["fast"], "fn": dependent},
        })
        self.assertTrue(all(item["status"] == "completed" for item in result.values()))

    def test_long_chain_success_and_skip(self):
        for failing in [False, True]:
            called = []

            def first():
                if failing:
                    raise ValueError("failure")
                return 0

            tasks = {"0": {"deps": [], "fn": first}}
            for i in range(1, 1500):
                tasks[str(i)] = {
                    "deps": [str(i - 1)],
                    "fn": lambda i=i: called.append(i) or i,
                }
            result = run_graph(tasks)
            self.assertEqual(len(result), 1500)
            self.assertEqual(result["1499"], {"status": "skipped"} if failing else
                             {"status": "completed", "value": 1499})
            self.assertEqual(called, [] if failing else list(range(1, 1500)))
