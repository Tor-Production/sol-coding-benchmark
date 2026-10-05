"""External behavioral checks. Run only after freezing the candidate."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import http.client
import importlib
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.parse import quote


class Intervals(unittest.TestCase):
    def setUp(self):
        self.merge = importlib.import_module("intervals").merge_intervals

    def test_empty_single(self):
        self.assertEqual(self.merge([]), [])
        self.assertEqual(self.merge(((4, 4),)), [[4, 4]])

    def test_overlap(self):
        self.assertEqual(self.merge([[9, 12], [1, 4], [3, 7]]), [[1, 7], [9, 12]])

    def test_adjacency_chain(self):
        self.assertEqual(self.merge([[5, 6], [1, 2], [3, 4]]), [[1, 6]])

    def test_nested_duplicates(self):
        self.assertEqual(self.merge([[1, 10], [2, 3], [1, 10], [9, 9]]), [[1, 10]])

    def test_negative_huge(self):
        n = 10 ** 80
        self.assertEqual(self.merge([[-4, -2], [-1, 1], [n, n + 1]]), [[-4, 1], [n, n + 1]])

    def test_purity_and_shapes(self):
        rows = [[5, 7], [1, 1], (3, 3)]
        before = copy.deepcopy(rows)
        result = self.merge(rows)
        self.assertEqual(result, [[1, 1], [3, 3], [5, 7]])
        self.assertEqual(rows, before)
        result[0][0] = -100
        self.assertEqual(rows, before)

    def test_endpoint_validation(self):
        for pair in ([True, 1], [1, False], [1.0, 2], ["1", 2], [3, 2], [None, 3]):
            with self.subTest(pair=pair), self.assertRaises(ValueError):
                self.merge([pair])

    def test_structure_validation(self):
        for rows in (None, {}, "12", [1], [[1]], [[1, 2, 3]], [{"a": 1, "b": 2}]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.merge(rows)

    def test_seeded_union_oracle(self):
        rng = random.Random(30092026)
        for _ in range(120):
            rows = [sorted([rng.randrange(-15, 16), rng.randrange(-15, 16)]) for _ in range(rng.randrange(20))]
            points = sorted({x for lo, hi in rows for x in range(lo, hi + 1)})
            expected = []
            for x in points:
                if expected and x == expected[-1][1] + 1:
                    expected[-1][1] = x
                else:
                    expected.append([x, x])
            self.assertEqual(self.merge(rows), expected)

    def test_large_input(self):
        rows = [[i * 2, i * 2 + 1] for i in reversed(range(30000))]
        self.assertEqual(self.merge(rows), [[0, 59999]])


class Cache(unittest.TestCase):
    def setUp(self):
        self.cls = importlib.import_module("cache").TTLCache
        self.now = 0.0
        self.c = self.cls(2, 10, lambda: self.now)

    def test_constructor_validation(self):
        for args in ((0, 10, lambda: 0), (-1, 10, lambda: 0), (True, 10, lambda: 0),
                     (1.0, 10, lambda: 0), (2, 0, lambda: 0), (2, -1, lambda: 0),
                     (2, float("inf"), lambda: 0), (2, float("nan"), lambda: 0),
                     (2, True, lambda: 0), (2, 10, 0)):
            with self.subTest(args=args[:2]), self.assertRaises(ValueError):
                self.cls(*args)

    def test_none_value_and_default(self):
        sentinel = object()
        self.c.put("a", None)
        self.assertIsNone(self.c.get("a", sentinel))
        self.assertIs(self.c.get("b", sentinel), sentinel)

    def test_expiry_boundary(self):
        self.c.put("a", 1)
        self.now = 9.999
        self.assertEqual(self.c.get("a"), 1)
        self.now = 10
        self.assertEqual(self.c.get("a", "gone"), "gone")

    def test_read_does_not_extend_ttl(self):
        self.c.put("a", 1)
        self.now = 9
        self.c.get("a")
        self.now = 10
        self.assertIsNone(self.c.get("a"))

    def test_overwrite_resets_ttl(self):
        self.c.put("a", 1)
        self.now = 8
        self.c.put("a", 2)
        self.now = 10
        self.assertEqual(self.c.get("a"), 2)
        self.now = 18
        self.assertIsNone(self.c.get("a"))

    def test_lru_reads(self):
        self.c.put("a", 1)
        self.c.put("b", 2)
        self.c.get("a")
        self.c.put("c", 3)
        self.assertIsNone(self.c.get("b"))
        self.assertEqual(self.c.get("a"), 1)

    def test_lru_writes(self):
        self.c.put("a", 1)
        self.c.put("b", 2)
        self.c.put("a", 4)
        self.c.put("c", 3)
        self.assertIsNone(self.c.get("b"))
        self.assertEqual(self.c.get("a"), 4)

    def test_expired_mru_does_not_evict_live(self):
        self.c.put("a", 1)
        self.now = 1
        self.c.put("b", 2)
        self.now = 2
        self.c.get("a")
        self.now = 10
        self.c.put("c", 3)
        self.assertEqual(self.c.get("b"), 2)
        self.assertEqual(self.c.get("c"), 3)
        self.assertEqual(len(self.c), 2)

    def test_delete_live_and_expired(self):
        self.c.put("a", 1)
        self.assertTrue(self.c.delete("a"))
        self.assertFalse(self.c.delete("a"))
        self.c.put("b", 2)
        self.now = 10
        self.assertFalse(self.c.delete("b"))

    def test_len_and_independent_instances(self):
        other = self.cls(1, 10, lambda: self.now)
        self.c.put(("tuple", 1), [1])
        self.c.put("b", 2)
        self.assertEqual(len(other), 0)
        self.now = 20
        self.assertEqual(len(self.c), 0)


class Graph(unittest.TestCase):
    def setUp(self):
        self.run = importlib.import_module("dag").run_graph

    def test_empty_sorted_and_values(self):
        self.assertEqual(self.run({}), {})
        result = self.run({"z": {"deps": [], "fn": lambda: None}, "a": {"deps": [], "fn": lambda: 0}})
        self.assertEqual(list(result), ["a", "z"])
        self.assertEqual(result["z"], {"status": "completed", "value": None})
        self.assertEqual(result["a"], {"status": "completed", "value": 0})

    def test_validate_before_any_side_effect(self):
        seen = []
        tasks = {"a": {"deps": [], "fn": lambda: seen.append(1)},
                 "b": {"deps": ["missing"], "fn": lambda: 2}}
        with self.assertRaises(ValueError):
            self.run(tasks)
        self.assertEqual(seen, [])

    def test_malformed_and_workers(self):
        for tasks in (None, [], {"": {"deps": [], "fn": lambda: 1}},
                      {"a": {}}, {"a": {"deps": [], "fn": 42}},
                      {"a": {"deps": (), "fn": lambda: 1}}):
            with self.subTest(tasks=tasks), self.assertRaises(ValueError):
                self.run(tasks)
        for workers in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                self.run({}, workers)

    def test_bad_dependency_lists(self):
        for deps in (["a"], ["b", "b"], [1], ["unknown"]):
            tasks = {"a": {"deps": deps, "fn": lambda: 1}, "b": {"deps": [], "fn": lambda: 2}}
            with self.subTest(deps=deps), self.assertRaises(ValueError):
                self.run(tasks)

    def test_disconnected_cycle_is_rejected(self):
        seen = []
        tasks = {"ok": {"deps": [], "fn": lambda: seen.append(1)},
                 "a": {"deps": ["b"], "fn": lambda: 2}, "b": {"deps": ["a"], "fn": lambda: 3}}
        with self.assertRaises(ValueError):
            self.run(tasks)
        self.assertEqual(seen, [])

    def test_deep_chain_purity_and_once(self):
        seen = []
        tasks = {f"t{i:04}": {"deps": [] if i == 0 else [f"t{i - 1:04}"],
                            "fn": lambda i=i: seen.append(i) or i} for i in range(1500)}
        deps = {k: v["deps"][:] for k, v in tasks.items()}
        result = self.run(tasks, 3)
        self.assertEqual(seen, list(range(1500)))
        self.assertEqual(result["t1499"]["value"], 1499)
        self.assertEqual({k: v["deps"] for k, v in tasks.items()}, deps)

    def test_failure_transitive_skip_and_unrelated(self):
        seen = []
        def fail():
            raise RuntimeError("expected failure")
        tasks = {"a": {"deps": [], "fn": fail},
                 "b": {"deps": ["a"], "fn": lambda: seen.append("b")},
                 "c": {"deps": ["b", "d"], "fn": lambda: seen.append("c")},
                 "d": {"deps": [], "fn": lambda: 8}}
        result = self.run(tasks)
        self.assertEqual(result["a"], {"status": "failed", "error": "expected failure"})
        self.assertEqual(result["b"], {"status": "skipped"})
        self.assertEqual(result["c"], {"status": "skipped"})
        self.assertEqual(result["d"]["value"], 8)
        self.assertEqual(seen, [])

    def test_independent_parallelism(self):
        barrier = threading.Barrier(2)
        def meet():
            barrier.wait(timeout=3)
            return True
        result = self.run({"a": {"deps": [], "fn": meet}, "b": {"deps": [], "fn": meet}}, 2)
        self.assertTrue(all(v == {"status": "completed", "value": True} for v in result.values()))

    def test_worker_cap(self):
        for workers in (1, 3):
            active = peak = 0
            lock = threading.Lock()
            def work():
                nonlocal active, peak
                with lock:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.005)
                with lock:
                    active -= 1
                return 1
            result = self.run({str(i): {"deps": [], "fn": work} for i in range(12)}, workers)
            self.assertLessEqual(peak, workers)
            self.assertEqual(active, 0)
            self.assertEqual(sum(v["value"] for v in result.values()), 12)

    def test_newly_ready_does_not_wait_for_unrelated(self):
        release = threading.Event()
        tasks = {"a_slow": {"deps": [], "fn": lambda: release.wait(timeout=3)},
                 "b_fast": {"deps": [], "fn": lambda: 1},
                 "c_unlock": {"deps": ["b_fast"], "fn": lambda: release.set() or 2}}
        result = self.run(tasks, 2)
        self.assertEqual(result["a_slow"], {"status": "completed", "value": True})


class Service(unittest.TestCase):
    def setUp(self):
        module = importlib.import_module("reservation.store")
        self.Store, self.Conflict, self.NotFound = module.Store, module.Conflict, module.NotFound
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / "service.db")
        self.s = self.Store(self.db)

    def server(self):
        factory = importlib.import_module("reservation.http_api").create_server
        server = factory(self.db)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        def close():
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
        self.addCleanup(close)
        return server.server_address

    def request(self, address, method, path, payload=None, raw=None):
        conn = http.client.HTTPConnection(*address, timeout=5)
        try:
            body = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else None)
            conn.request(method, path, body=body, headers={"Content-Type": "application/json"})
            response = conn.getresponse()
            content = response.read()
            self.assertIn("application/json", response.getheader("Content-Type", ""))
            self.assertEqual(int(response.getheader("Content-Length", "-1")), len(content))
            return response.status, json.loads(content)
        finally:
            conn.close()

    def cli(self, *args):
        return subprocess.run([sys.executable, "-m", "reservation", "--db", self.db, *args],
                              cwd=WORKSPACE, capture_output=True, text=True, timeout=8)

    def test_add_get_and_normalization(self):
        self.assertEqual(self.s.add_item("  товар  ", 5), {"sku": "товар", "available": 5})
        self.assertEqual(self.s.add_item("товар", 2)["available"], 7)
        self.assertEqual(self.s.get_item(" товар ")["available"], 7)

    def test_validation(self):
        for sku, quantity in (("", 1), ("  ", 1), (None, 1), ("a", 0), ("a", -1), ("a", True), ("a", 1.0)):
            with self.subTest(sku=sku, quantity=quantity), self.assertRaises(ValueError):
                self.s.add_item(sku, quantity)
        self.s.add_item("a", 2)
        for key, quantity in (("", 1), (None, 1), ("k", True), ("k", 0)):
            with self.assertRaises(ValueError):
                self.s.reserve(key, "a", quantity)
        for value in (0, -1, True, "1"):
            with self.assertRaises(ValueError):
                self.s.release(value)

    def test_reservation_record(self):
        self.s.add_item("a", 5)
        r = self.s.reserve(" k ", " a ", 2)
        self.assertIs(type(r["reservation_id"]), int)
        self.assertGreater(r["reservation_id"], 0)
        self.assertEqual(r, {"reservation_id": r["reservation_id"], "idempotency_key": "k", "sku": "a", "quantity": 2, "status": "active"})
        self.assertEqual(self.s.get_item("a")["available"], 3)

    def test_idempotency(self):
        self.s.add_item("a", 4)
        first = self.s.reserve("key", "a", 3)
        self.assertEqual(self.s.reserve("key", "a", 3), first)
        self.assertEqual(self.s.get_item("a")["available"], 1)
        self.assertEqual(self.s.report()["active_reservations"], 1)

    def test_key_conflict_is_atomic(self):
        self.s.add_item("a", 10)
        self.s.add_item("b", 10)
        self.s.reserve("key", "a", 2)
        before = self.s.report()
        for sku, qty in (("a", 3), ("b", 2)):
            with self.assertRaises(self.Conflict):
                self.s.reserve("key", sku, qty)
        self.assertEqual(self.s.report(), before)

    def test_oversell_rollback(self):
        self.s.add_item("a", 2)
        with self.assertRaises(self.Conflict):
            self.s.reserve("key", "a", 3)
        self.assertEqual(self.s.report()["active_reservations"], 0)
        self.assertEqual(self.s.get_item("a")["available"], 2)
        self.assertEqual(self.s.reserve("key", "a", 2)["quantity"], 2)

    def test_release_exactly_once(self):
        self.s.add_item("a", 5)
        r = self.s.reserve("key", "a", 2)
        first = self.s.release(r["reservation_id"])
        self.assertEqual(first["status"], "released")
        self.assertEqual(self.s.release(r["reservation_id"]), first)
        self.assertEqual(self.s.get_item("a")["available"], 5)

    def test_retry_after_release(self):
        self.s.add_item("a", 5)
        r = self.s.reserve("key", "a", 2)
        released = self.s.release(r["reservation_id"])
        self.assertEqual(self.s.reserve("key", "a", 2), released)
        self.assertEqual(self.s.get_item("a")["available"], 5)

    def test_persistence(self):
        self.s.add_item("a", 5)
        r = self.s.reserve("key", "a", 2)
        reopened = self.Store(self.db)
        self.assertEqual(reopened.get_item("a")["available"], 3)
        self.assertEqual(reopened.reserve("key", "a", 2), r)

    def test_sorted_report(self):
        self.assertEqual(self.s.report(), {"items": [], "active_reservations": 0, "reserved_units": 0})
        self.s.add_item("z", 5)
        self.s.add_item("a", 7)
        self.s.reserve("k1", "z", 2)
        r = self.s.reserve("k2", "a", 3)
        self.s.release(r["reservation_id"])
        self.assertEqual(self.s.report(), {"items": [{"sku": "a", "available": 7}, {"sku": "z", "available": 3}], "active_reservations": 1, "reserved_units": 2})

    def test_not_found(self):
        with self.assertRaises(self.NotFound):
            self.s.get_item("missing")
        with self.assertRaises(self.NotFound):
            self.s.reserve("key", "missing", 1)
        with self.assertRaises(self.NotFound):
            self.s.release(123)

    def test_concurrent_no_oversell(self):
        self.s.add_item("a", 7)
        stores = [self.Store(self.db) for _ in range(16)]
        def attempt(i):
            try:
                return stores[i].reserve(str(i), "a", 1)
            except self.Conflict:
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt, range(16)))
        self.assertEqual(sum(r is not None for r in results), 7)
        self.assertEqual(self.s.get_item("a")["available"], 0)

    def test_concurrent_same_key(self):
        self.s.add_item("a", 20)
        stores = [self.Store(self.db) for _ in range(12)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda s: s.reserve("same", "a", 3), stores))
        self.assertTrue(all(r == results[0] for r in results))
        self.assertEqual(self.s.get_item("a")["available"], 17)
        self.assertEqual(self.s.report()["active_reservations"], 1)

    def test_http_health_and_unknown(self):
        address = self.server()
        self.assertEqual(self.request(address, "GET", "/health"), (200, {"ok": True}))
        status, error = self.request(address, "GET", "/unknown")
        self.assertEqual(status, 404)
        self.assertIsInstance(error["error"], str)
        self.assertEqual(self.request(address, "GET", "/health")[0], 200)

    def test_http_end_to_end(self):
        address = self.server()
        self.assertEqual(self.request(address, "POST", "/items", {"sku": "a", "quantity": 5})[0], 201)
        payload = {"idempotency_key": "k", "sku": "a", "quantity": 2}
        status, r = self.request(address, "POST", "/reservations", payload)
        self.assertEqual(status, 201)
        self.assertEqual(self.request(address, "POST", "/reservations", payload), (201, r))
        self.assertEqual(self.request(address, "GET", "/items/a"), (200, {"sku": "a", "available": 3}))
        route = f"/reservations/{r['reservation_id']}/release"
        self.assertEqual(self.request(address, "POST", route, {})[1]["status"], "released")
        self.assertEqual(self.request(address, "POST", route, {})[0], 200)
        self.assertEqual(self.request(address, "GET", "/report")[1]["reserved_units"], 0)

    def test_http_bad_inputs(self):
        address = self.server()
        for raw in (b"{broken", b"[]", b"null", b"{}", b'{"sku":"a","quantity":true}', b'{"sku":"a","quantity":0}'):
            status, error = self.request(address, "POST", "/items", raw=raw)
            self.assertEqual(status, 400)
            self.assertIsInstance(error["error"], str)
        self.assertEqual(self.request(address, "GET", "/health")[0], 200)

    def test_http_domain_errors(self):
        address = self.server()
        self.assertEqual(self.request(address, "GET", "/items/missing")[0], 404)
        self.s.add_item("a", 1)
        self.assertEqual(self.request(address, "POST", "/reservations", {"idempotency_key": "k", "sku": "a", "quantity": 2})[0], 409)
        self.assertEqual(self.request(address, "POST", "/reservations/999/release", {})[0], 404)

    def test_http_unicode(self):
        address = self.server()
        sku = "товар з пробілом/частина"
        self.s.add_item(sku, 2)
        self.assertEqual(self.request(address, "GET", "/items/" + quote(sku, safe="")), (200, {"sku": sku, "available": 2}))

    def test_http_large_body(self):
        address = self.server()
        self.assertEqual(self.request(address, "POST", "/items", raw=b"x" * 65537)[0], 413)

    def test_cli_roundtrip_and_shared_state(self):
        added = self.cli("add", "--sku", "a", "--quantity", "6")
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertEqual(json.loads(added.stdout), {"sku": "a", "available": 6})
        reserved = self.cli("reserve", "--key", "key", "--sku", "a", "--quantity", "2")
        self.assertEqual(reserved.returncode, 0, reserved.stderr)
        r = json.loads(reserved.stdout)
        self.assertEqual(self.s.get_item("a")["available"], 4)
        address = self.server()
        self.assertEqual(self.request(address, "GET", "/report")[1]["reserved_units"], 2)
        released = self.cli("release", "--id", str(r["reservation_id"]))
        self.assertEqual(released.returncode, 0, released.stderr)
        report = self.cli("report")
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertEqual(json.loads(report.stdout)["items"][0]["available"], 6)

    def test_cli_failure_json(self):
        for args in (("add", "--sku", "a", "--quantity", "0"), ("release", "--id", "999"),
                     ("reserve", "--key", "k", "--sku", "missing", "--quantity", "1")):
            result = self.cli(*args)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIsInstance(json.loads(result.stderr)["error"], str)


class LedgerResult(unittest.TestResult):
    def __init__(self):
        super().__init__()
        self.records = {}

    def startTest(self, test):
        super().startTest(test)
        self.records[test.id()] = {"test": test.id().split(".", 1)[-1], "passed": True, "detail": None}

    def failed(self, test, detail):
        parent = test.test_case if isinstance(test, unittest.case._SubTest) else test
        self.records[parent.id()].update(passed=False, detail=detail[-3000:])

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.failed(test, self._exc_info_to_string(err, test))

    def addError(self, test, err):
        super().addError(test, err)
        self.failed(test, self._exc_info_to_string(err, test))

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err:
            self.failed(test, self._exc_info_to_string(err, test))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True, choices=["01-easy", "02-medium", "03-hard", "04-components"])
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    WORKSPACE = str(Path(args.workspace).resolve())
    sys.path.insert(0, WORKSPACE)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase({"01-easy": Intervals, "02-medium": Cache, "03-hard": Graph, "04-components": Service}[args.task])
    result = LedgerResult()
    suite.run(result)
    records = list(result.records.values())
    passed = sum(r["passed"] for r in records)
    report = {"task": args.task, "tests": records, "passed": passed, "total": len(records),
              "score": round(100 * passed / len(records), 6), "accepted": passed == len(records)}
    Path(args.output).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "tests"}))
    raise SystemExit(0 if report["accepted"] else 1)
