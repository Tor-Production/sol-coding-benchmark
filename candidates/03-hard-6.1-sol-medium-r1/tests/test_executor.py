import threading
import unittest

from dag import run_graph


class ExecutorTests(unittest.TestCase):
    def test_invalid_inputs_never_execute_functions(self):
        calls = []

        def task(deps=None):
            return {"deps": [] if deps is None else deps, "fn": lambda: calls.append(1)}

        invalid = [
            None, [], {"": task()}, {1: task()}, {"a": None},
            {"a": {}}, {"a": {"deps": []}}, {"a": {"fn": lambda: None}},
            {"a": {"deps": (), "fn": lambda: None}},
            {"a": {"deps": [], "fn": 1}},
            {"a": task([[]])}, {"a": task([1])}, {"a": task([""])},
            {"a": task(["a"])}, {"a": task(["missing"])},
            {"a": task(), "b": task(["a", "a"])},
            {"root": task(), "a": task(["b"]), "b": task(["a"])},
            {"root": task(), "z": {"deps": [], "fn": None}},
        ]
        for graph in invalid:
            with self.subTest(graph=graph):
                with self.assertRaises(ValueError):
                    run_graph(graph)
        self.assertEqual(calls, [])

    def test_worker_validation_and_empty_graph(self):
        for workers in [True, False, 0, -1, 1.0, "2", None]:
            with self.subTest(workers=workers):
                with self.assertRaises(ValueError):
                    run_graph({}, workers)
        self.assertEqual(run_graph({}), {})

    def test_independent_tasks_overlap_with_worker_bound(self):
        barrier = threading.Barrier(2)
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
                return 42
            finally:
                with lock:
                    active -= 1

        graph = {str(i): {"deps": [], "fn": work} for i in range(6)}
        result = run_graph(graph, max_workers=2)
        self.assertEqual(peak, 2)
        self.assertTrue(all(r == {"status": "completed", "value": 42}
                            for r in result.values()))

    def test_dependent_starts_while_unrelated_task_is_running(self):
        slow_started = threading.Event()
        dependent_started = threading.Event()

        def slow():
            slow_started.set()
            if not dependent_started.wait(5):
                raise RuntimeError("dependent did not start while a worker was free")
            return "slow finished"

        def fast():
            if not slow_started.wait(5):
                raise RuntimeError("independent tasks did not overlap")

        def dependent():
            dependent_started.set()
            return "dependent finished"

        result = run_graph({
            "a_slow": {"deps": [], "fn": slow},
            "b_fast": {"deps": [], "fn": fast},
            "c_dependent": {"deps": ["b_fast"], "fn": dependent},
        })
        self.assertEqual(result["a_slow"], {"status": "completed", "value": "slow finished"})
        self.assertEqual(result["c_dependent"]["status"], "completed")

    def test_failure_skips_transitive_descendants_with_multiple_parents(self):
        called = []

        def fail():
            called.append("failure")
            raise RuntimeError("broken")

        def work(name):
            return lambda: called.append(name) or name

        graph = {
            "z": {"deps": [], "fn": work("z")},
            "failure": {"deps": [], "fn": fail},
            "parent": {"deps": [], "fn": work("parent")},
            "join": {"deps": ["failure", "parent"], "fn": work("join")},
            "child": {"deps": ["join", "z"], "fn": work("child")},
            "ok": {"deps": ["parent"], "fn": work("ok")},
        }
        result = run_graph(graph)
        self.assertEqual(list(result), sorted(graph))
        self.assertEqual(result["failure"], {"status": "failed", "error": "broken"})
        self.assertEqual(result["join"], {"status": "skipped"})
        self.assertEqual(result["child"], {"status": "skipped"})
        self.assertCountEqual(called, ["failure", "parent", "ok", "z"])
        self.assertEqual(result["ok"], {"status": "completed", "value": "ok"})

    def test_all_dependencies_succeed_and_inputs_are_preserved(self):
        parents = set()

        def parent(name):
            return lambda: parents.add(name)

        def join():
            self.assertEqual(parents, {"a", "b"})
            return None

        deps = ["a", "b"]
        graph = {
            "join": {"deps": deps, "fn": join},
            "a": {"deps": [], "fn": parent("a")},
            "b": {"deps": [], "fn": parent("b")},
        }
        before = {key: dict(value, deps=list(value["deps"])) for key, value in graph.items()}
        result = run_graph(graph, 1)
        self.assertEqual(result["join"], {"status": "completed", "value": None})
        self.assertEqual(graph, before)
        self.assertIs(graph["join"]["deps"], deps)

    def test_long_chain(self):
        called = []
        graph = {
            str(i): {"deps": [str(i - 1)] if i else [],
                     "fn": lambda i=i: called.append(i) or i}
            for i in range(1500)
        }
        result = run_graph(graph)
        self.assertEqual(called, list(range(1500)))
        self.assertEqual(result["1499"], {"status": "completed", "value": 1499})


if __name__ == "__main__":
    unittest.main()
