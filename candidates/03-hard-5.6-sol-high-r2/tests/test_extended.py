import threading
import unittest

from dag import run_graph


class ExtendedTests(unittest.TestCase):
    def test_entire_graph_is_validated_before_execution(self):
        called = []
        tasks = {
            "valid": {"deps": [], "fn": lambda: called.append(True)},
            "invalid": {"deps": ["missing"], "fn": lambda: None},
        }

        with self.assertRaises(ValueError):
            run_graph(tasks)
        self.assertEqual(called, [])

    def test_failure_skips_all_descendants_but_not_independent_work(self):
        called = []

        def fail():
            raise RuntimeError("broken")

        tasks = {
            "a": {"deps": [], "fn": fail},
            "b": {"deps": [], "fn": lambda: 2},
            "c": {"deps": ["a", "b"], "fn": lambda: called.append("c")},
            "d": {"deps": ["c"], "fn": lambda: called.append("d")},
        }

        self.assertEqual(
            run_graph(tasks),
            {
                "a": {"status": "failed", "error": "broken"},
                "b": {"status": "completed", "value": 2},
                "c": {"status": "skipped"},
                "d": {"status": "skipped"},
            },
        )
        self.assertEqual(called, [])

    def test_newly_ready_branch_does_not_wait_for_slow_peer(self):
        slow_started = threading.Event()
        release_slow = threading.Event()
        dependent_started = threading.Event()
        finished = threading.Event()
        outcome = []

        def fast():
            self.assertTrue(slow_started.wait(3))
            return "fast"

        def slow():
            slow_started.set()
            self.assertTrue(release_slow.wait(3))
            return "slow"

        def dependent():
            dependent_started.set()
            return "dependent"

        tasks = {
            "a-fast": {"deps": [], "fn": fast},
            "b-slow": {"deps": [], "fn": slow},
            "c-dependent": {"deps": ["a-fast"], "fn": dependent},
        }

        def execute():
            try:
                outcome.append(run_graph(tasks, max_workers=2))
            finally:
                finished.set()

        runner = threading.Thread(target=execute)
        runner.start()
        try:
            self.assertTrue(dependent_started.wait(3))
        finally:
            release_slow.set()
            runner.join(3)

        self.assertTrue(finished.is_set())
        self.assertEqual(outcome[0]["c-dependent"]["status"], "completed")

    def test_deep_chain_and_sorted_result_order(self):
        tasks = {}
        for index in range(1500):
            task_id = f"task-{index:04d}"
            deps = [] if index == 0 else [f"task-{index - 1:04d}"]
            tasks[task_id] = {"deps": deps, "fn": lambda index=index: index}

        result = run_graph(tasks, max_workers=4)
        self.assertEqual(list(result), sorted(tasks))
        self.assertEqual(
            result["task-1499"], {"status": "completed", "value": 1499}
        )

    def test_inputs_are_not_mutated(self):
        deps = ["a"]
        tasks = {
            "a": {"deps": [], "fn": lambda: 1},
            "b": {"deps": deps, "fn": lambda: 2},
        }
        run_graph(tasks)
        self.assertEqual(deps, ["a"])
        self.assertIs(tasks["b"]["deps"], deps)

    def test_invalid_worker_counts(self):
        for value in (True, False, 0, -1, 1.5, "2", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                run_graph({}, value)


if __name__ == "__main__":
    unittest.main()
