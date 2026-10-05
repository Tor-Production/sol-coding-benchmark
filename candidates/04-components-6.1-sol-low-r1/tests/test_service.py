import concurrent.futures
import http.client
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from reservation.http_api import create_server
from reservation.store import Conflict, NotFound, Store


class ServiceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = str(Path(tmp.name) / "stock.db")
        self.store = Store(self.path)

    def test_persistence_retries_and_validation(self):
        self.store.add_item(" b ", 5)
        self.store.add_item("a", 1)
        record = self.store.reserve(" key ", " b ", 2)
        reopened = Store(self.path)
        self.assertEqual(reopened.reserve("key", "b", 2), record)
        with self.assertRaises(Conflict):
            reopened.reserve("key", "b", 1)
        released = reopened.release(record["reservation_id"])
        self.assertEqual(reopened.reserve("key", "b", 2), released)
        self.assertEqual(reopened.release(record["reservation_id"]), released)
        self.assertEqual(reopened.report(), {"items": [{"sku": "a", "available": 1}, {"sku": "b", "available": 5}], "active_reservations": 0, "reserved_units": 0})
        for value in (True, 0, -1, 1.5, "2", None):
            with self.assertRaises(ValueError):
                reopened.add_item("b", value)
            with self.assertRaises(ValueError):
                reopened.release(value)
        with self.assertRaises(NotFound):
            reopened.reserve("missing", "unknown", 1)

    def test_concurrent_stock_and_idempotency(self):
        self.store.add_item("sku", 10)
        def reserve(index):
            try:
                return Store(self.path).reserve(str(index), "sku", 1)
            except Conflict:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(reserve, range(30)))
        self.assertEqual(sum(r is not None for r in results), 10)
        self.assertEqual(self.store.report()["reserved_units"], 10)
        self.store.add_item("sku", 5)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            records = list(pool.map(lambda _: Store(self.path).reserve("same", "sku", 2), range(16)))
            list(pool.map(lambda _: Store(self.path).release(records[0]["reservation_id"]), range(16)))
        self.assertTrue(all(r == records[0] for r in records))
        self.assertEqual(self.store.get_item("sku")["available"], 5)

    def test_http_errors_and_shared_state(self):
        server = create_server(self.path)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        def cleanup():
            server.shutdown()
            thread.join()
            server.server_close()
        self.addCleanup(cleanup)
        def request(method, path, body=None, headers=None):
            connection = http.client.HTTPConnection(*server.server_address, timeout=5)
            try:
                connection.request(method, path, body, headers or {})
                response = connection.getresponse()
                data = response.read()
                self.assertEqual(int(response.getheader("Content-Length")), len(data))
                self.assertEqual(response.getheader("Content-Type"), "application/json")
                return response.status, json.loads(data)
            finally:
                connection.close()
        self.assertEqual(request("POST", "/items", json.dumps({"sku": "café/x", "quantity": 3}))[0], 201)
        self.assertEqual(request("GET", "/items/caf%C3%A9%2Fx"), (200, {"sku": "café/x", "available": 3}))
        for body in ("{", "[]", "{}", '{"sku":"s","quantity":true}'):
            self.assertEqual(request("POST", "/items", body)[0], 400)
        self.assertEqual(request("POST", "/items", b"", {"Content-Length": "65537"})[0], 413)
        self.assertEqual(request("GET", "/missing")[0], 404)
        self.assertEqual(request("GET", "/health"), (200, {"ok": True}))
        status, record = request("POST", "/reservations", json.dumps({"idempotency_key": "k", "sku": "café/x", "quantity": 2}))
        self.assertEqual(status, 201)
        self.assertEqual(self.store.get_item("café/x")["available"], 1)
        self.assertEqual(request("POST", f'/reservations/{record["reservation_id"]}/release', "{}")[0], 200)

    def test_cli_json_and_errors(self):
        def run(*args):
            return subprocess.run([sys.executable, "-m", "reservation", "--db", self.path, *args], capture_output=True, text=True)
        result = run("add", "--sku", "s", "--quantity", "3")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {"sku": "s", "available": 3})
        for args in (("add", "--sku", "s", "--quantity", "0"), ("release", "--id", "99"), ("release", "--id", "bad")):
            result = run(*args)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn("error", json.loads(result.stderr))
