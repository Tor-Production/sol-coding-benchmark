from concurrent.futures import ThreadPoolExecutor
import http.client
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.parse import quote

from reservation.http_api import create_server
from reservation.store import Conflict, NotFound, Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "stock.db")
        self.store = Store(self.path)

    def test_persistence_and_idempotency(self):
        self.assertEqual(self.store.add_item(" b ", 4), {"sku": "b", "available": 4})
        self.store.add_item("b", 2)
        first = self.store.reserve(" k ", " b ", 3)
        reopened = Store(self.path)
        self.assertEqual(reopened.reserve("k", "b", 3), first)
        with self.assertRaises(Conflict):
            reopened.reserve("k", "missing", 3)
        released = reopened.release(first["reservation_id"])
        self.assertEqual(reopened.release(first["reservation_id"]), released)
        self.assertEqual(Store(self.path).reserve("k", "b", 3), released)
        self.assertEqual(reopened.report(), {"items": [{"sku": "b", "available": 6}],
                                            "active_reservations": 0, "reserved_units": 0})

    def test_failure_rolls_back_and_report(self):
        self.store.add_item("z", 5)
        self.store.add_item("a", 1)
        before = self.store.report()
        with self.assertRaises(Conflict):
            self.store.reserve("key", "z", 6)
        with self.assertRaises(NotFound):
            self.store.reserve("key", "missing", 1)
        with self.assertRaises(NotFound):
            self.store.release(1)
        self.assertEqual(before, self.store.report())
        self.store.reserve("key", "z", 2)
        report = self.store.report()
        self.assertEqual([item["sku"] for item in report["items"]], ["a", "z"])
        self.assertEqual((report["active_reservations"], report["reserved_units"]), (1, 2))

    def test_validation(self):
        for value in (None, True, 0, -1, 1.5, "1"):
            with self.subTest(value=value):
                for call in (lambda: self.store.add_item("sku", value),
                             lambda: self.store.reserve("key", "sku", value),
                             lambda: self.store.release(value)):
                    with self.assertRaises(ValueError):
                        call()
        for value in (None, 1, "", " \t"):
            with self.assertRaises(ValueError):
                self.store.get_item(value)
            with self.assertRaises(ValueError):
                self.store.reserve(value, "sku", 1)

    def test_concurrent_add_reserve_and_release(self):
        def add(_):
            return Store(self.path).add_item("sku", 1)
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(add, range(20)))
        self.assertEqual(self.store.get_item("sku")["available"], 20)

        def reserve(index):
            try:
                return Store(self.path).reserve(str(index), "sku", 1)
            except Conflict:
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            records = [r for r in pool.map(reserve, range(40)) if r]
        self.assertEqual(len(records), 20)
        self.assertEqual(self.store.report()["reserved_units"], 20)
        identifier = records[0]["reservation_id"]
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: Store(self.path).release(identifier), range(16)))
        self.assertEqual(self.store.get_item("sku")["available"], 1)

    def test_concurrent_same_key(self):
        self.store.add_item("sku", 20)
        with ThreadPoolExecutor(max_workers=8) as pool:
            records = list(pool.map(lambda _: Store(self.path).reserve("key", "sku", 2), range(20)))
        self.assertTrue(all(record == records[0] for record in records))
        self.assertEqual(self.store.get_item("sku")["available"], 18)
        self.assertEqual(self.store.report()["active_reservations"], 1)


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "stock.db")
        self.server = create_server(self.path)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, method, path, body=None, raw=None, headers=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        try:
            payload = raw if raw is not None else (json.dumps(body) if body is not None else None)
            connection.request(method, path, payload, headers or {})
            response = connection.getresponse()
            content = response.read()
            self.assertEqual(response.getheader("Content-Type"), "application/json")
            self.assertEqual(int(response.getheader("Content-Length")), len(content))
            return response.status, json.loads(content)
        finally:
            connection.close()

    def test_routes_and_shared_state(self):
        self.assertEqual(self.request("GET", "/health"), (200, {"ok": True}))
        sku = "café / part"
        self.assertEqual(self.request("POST", "/items", {"sku": sku, "quantity": 4}),
                         (201, {"sku": sku, "available": 4}))
        self.assertEqual(self.request("GET", "/items/" + quote(sku, safe=""))[0], 200)
        body = {"idempotency_key": "key", "sku": sku, "quantity": 3}
        status, record = self.request("POST", "/reservations", body)
        self.assertEqual(status, 201)
        self.assertEqual(self.request("POST", "/reservations", body), (201, record))
        status, released = self.request("POST", f"/reservations/{record['reservation_id']}/release", {})
        self.assertEqual((status, released["status"]), (200, "released"))
        self.assertEqual(self.request("GET", "/report")[1], Store(self.path).report())

    def test_errors_and_server_survival(self):
        for raw in ("{", "[]", "null", "{}", '{"sku":"a","quantity":true}'):
            status, record = self.request("POST", "/items", raw=raw)
            self.assertEqual(status, 400)
            self.assertEqual(set(record), {"error"})
        self.assertEqual(self.request("POST", "/items", raw=b"\xff")[0], 400)
        self.assertEqual(self.request("POST", "/items", raw="x" * 65537)[0], 413)
        self.assertEqual(self.request("POST", "/items", raw="", headers={"Content-Length": "65537"})[0], 413)
        self.assertEqual(self.request("POST", "/unknown", raw="", headers={"Content-Length": "65537"})[0], 413)
        boundary = '{"sku":"boundary","quantity":1}'
        self.assertEqual(self.request("POST", "/items", raw=boundary.ljust(65536))[0], 201)
        self.assertEqual(self.request("POST", "/items", raw="", headers={"Content-Length": "-1"})[0], 400)
        self.assertEqual(self.request("GET", "/unknown")[0], 404)
        self.assertEqual(self.request("PUT", "/items", {})[0], 404)
        self.assertEqual(self.request("GET", "/items/missing")[0], 404)
        self.assertEqual(self.request("POST", "/reservations/abc/release", {})[0], 400)
        self.request("POST", "/items", {"sku": "a", "quantity": 1})
        self.assertEqual(self.request("POST", "/reservations", {
            "idempotency_key": "key", "sku": "a", "quantity": 2})[0], 409)
        self.assertEqual(self.request("GET", "/health"), (200, {"ok": True}))

    def test_concurrent_requests(self):
        Store(self.path).add_item("sku", 4)
        def reserve(index):
            return self.request("POST", "/reservations", {
                "idempotency_key": str(index), "sku": "sku", "quantity": 1})[0]
        with ThreadPoolExecutor(max_workers=8) as pool:
            statuses = list(pool.map(reserve, range(12)))
        self.assertEqual(statuses.count(201), 4)
        self.assertEqual(statuses.count(409), 8)
        self.assertEqual(Store(self.path).get_item("sku")["available"], 0)


class CliTests(unittest.TestCase):
    def test_commands_and_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "stock.db")
            def run(*args):
                return subprocess.run([sys.executable, "-m", "reservation", "--db", path, *args],
                                      capture_output=True, text=True, timeout=10)
            result = run("add", "--sku", "sku", "--quantity", "5")
            self.assertEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout), {"sku": "sku", "available": 5})
            result = run("reserve", "--key", "k", "--sku", "sku", "--quantity", "2")
            self.assertEqual(result.returncode, 0)
            identifier = json.loads(result.stdout)["reservation_id"]
            result = run("release", "--id", str(identifier))
            self.assertEqual(json.loads(result.stdout)["status"], "released")
            result = run("report")
            self.assertEqual(json.loads(result.stdout), Store(path).report())
            for args in (("release", "--id", "999"), ("add", "--sku", "sku", "--quantity", "0"),
                         ("add", "--sku", "sku", "--quantity", "bad"), ("reserve",)):
                result = run(*args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertEqual(set(json.loads(result.stderr)), {"error"})
