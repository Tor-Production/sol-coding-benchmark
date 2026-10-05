import threading
import unittest

from dag import run_graph


class AdditionalTests(unittest.TestCase):
    def test_long_chain_is_iterative(self):
        tasks = {}
        for number in range(1500):
            task_id = f"t{number:04d}"
            deps = [] if number == 0 else [f"t{number - 1:04d}"]
            tasks[task_id] = {"deps": deps, "fn": lambda n=number: n}
        results = run_graph(tasks, max_workers=4)
        self.assertEqual(results["t1499"], {"status": "completed", "value": 1499})

    def test_failure_skips_all_descendants_but_not_independent_tasks(self):
        called = []

        def fail():
            raise RuntimeError("broken")

        tasks = {
            "failed": {"deps": [], "fn": fail},
            "child": {"deps": ["failed"], "fn": lambda: called.append("child")},
            "shared": {"deps": ["child", "ok"], "fn": lambda: called.append("shared")},
            "ok": {"deps": [], "fn": lambda: 7},
        }
        results = run_graph(tasks)
        self.assertEqual(results["failed"], {"status": "failed", "error": "broken"})
        self.assertEqual(results["child"], {"status": "skipped"})
        self.assertEqual(results["shared"], {"status": "skipped"})
        self.assertEqual(results["ok"], {"status": "completed", "value": 7})
        self.assertEqual(called, [])
        self.assertEqual(list(results), sorted(results))

    def test_entire_graph_is_validated_before_execution(self):
        called = []
        tasks = {
            "valid": {"deps": [], "fn": lambda: called.append(True)},
            "bad": {"deps": ["missing"], "fn": lambda: None},
        }
        with self.assertRaises(ValueError):
            run_graph(tasks)
        self.assertEqual(called, [])

    def test_newly_ready_work_does_not_wait_for_slow_unrelated_work(self):
        release_slow = threading.Event()
        dependent_ran = threading.Event()

        def slow():
            self.assertTrue(release_slow.wait(2))

        def dependent():
            dependent_ran.set()

        tasks = {
            "fast": {"deps": [], "fn": lambda: None},
            "slow": {"deps": [], "fn": slow},
            "dependent": {"deps": ["fast"], "fn": dependent},
        }
        holder = {}
        thread = threading.Thread(target=lambda: holder.setdefault("result", run_graph(tasks)))
        thread.start()
        try:
            self.assertTrue(dependent_ran.wait(1))
        finally:
            release_slow.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
