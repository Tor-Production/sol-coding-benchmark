import concurrent.futures
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


class ServiceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = str(Path(tmp.name) / "inventory.db")
        self.store = Store(self.path)

    def test_persistence_idempotency_and_failure_atomicity(self):
        self.assertEqual(self.store.add_item(" b ", 3), {"sku": "b", "available": 3})
        self.store.add_item("b", 2)
        self.store.add_item("a", 1)
        reservation = self.store.reserve(" key ", " b ", 4)
        reopened = Store(self.path)
        self.assertEqual(reopened.reserve("key", "b", 4), reservation)
        before = reopened.report()
        for key, sku, quantity, error in [("key", "b", 3, Conflict),
                                          ("new", "b", 2, Conflict),
                                          ("missing", "absent", 1, NotFound)]:
            with self.assertRaises(error):
                reopened.reserve(key, sku, quantity)
            self.assertEqual(reopened.report(), before)
        released = reopened.release(reservation["reservation_id"])
        self.assertEqual(released["status"], "released")
        self.assertEqual(reopened.release(reservation["reservation_id"]), released)
        self.assertEqual(Store(self.path).reserve("key", "b", 4), released)
        self.assertEqual(reopened.report(), {"items": [{"sku": "a", "available": 1},
                                                      {"sku": "b", "available": 5}],
                                             "active_reservations": 0, "reserved_units": 0})

    def test_validation(self):
        for invalid in [None, "", "  ", 1, True]:
            with self.assertRaises(ValueError):
                self.store.add_item(invalid, 1)
            with self.assertRaises(ValueError):
                self.store.reserve(invalid, "sku", 1)
        for invalid in [None, 0, -1, True, 1.0, "1"]:
            with self.assertRaises(ValueError):
                self.store.add_item("sku", invalid)
            with self.assertRaises(ValueError):
                self.store.reserve("key", "sku", invalid)
            with self.assertRaises(ValueError):
                self.store.release(invalid)
        with self.assertRaises(NotFound):
            self.store.release(1)
        self.assertEqual(self.store.report()["items"], [])

    def test_concurrent_instances(self):
        self.store.add_item("sku", 10)
        def reserve(i):
            try:
                return Store(self.path).reserve(str(i), "sku", 1)
            except Conflict:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            records = list(pool.map(reserve, range(30)))
        records = [r for r in records if r]
        self.assertEqual(len(records), 10)
        self.assertEqual(self.store.get_item("sku")["available"], 0)
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(lambda _: Store(self.path).release(records[0]["reservation_id"]), range(20)))
            retries = list(pool.map(lambda _: Store(self.path).reserve("same", "sku", 1), range(20)))
        self.assertTrue(all(r == retries[0] for r in retries))
        self.assertEqual(self.store.report()["active_reservations"], 10)
        self.assertEqual(self.store.get_item("sku")["available"], 0)

    def start_server(self):
        server = create_server(self.path)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        def cleanup():
            server.shutdown()
            server.server_close()
            thread.join()
        self.addCleanup(cleanup)
        return server.server_address

    def request(self, address, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection(*address, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            raw = response.read()
            self.assertEqual(response.getheader("Content-Type"), "application/json")
            self.assertEqual(int(response.getheader("Content-Length")), len(raw))
            return response.status, json.loads(raw)
        finally:
            connection.close()

    def test_http_routes_and_errors(self):
        address = self.start_server()
        self.assertEqual(self.request(address, "GET", "/health"), (200, {"ok": True}))
        sku = "café / widget"
        status, item = self.request(address, "POST", "/items", json.dumps({"sku": sku, "quantity": 3}))
        self.assertEqual(status, 201)
        self.assertEqual(self.request(address, "GET", "/items/" + quote(sku, safe="")), (200, item))
        payload = json.dumps({"idempotency_key": "k", "sku": sku, "quantity": 2})
        status, record = self.request(address, "POST", "/reservations", payload)
        self.assertEqual(status, 201)
        self.assertEqual(self.request(address, "POST", "/reservations", payload), (201, record))
        status, released = self.request(address, "POST", f'/reservations/{record["reservation_id"]}/release', '{}')
        self.assertEqual((status, released["status"]), (200, "released"))
        for method, path, body, expected in [
            ("GET", "/missing", None, 404), ("GET", "/items/missing", None, 404),
            ("POST", "/items", "{", 400), ("POST", "/items", "[]", 400),
            ("POST", "/items", "{}", 400),
            ("POST", "/items", '{"sku":"x","quantity":true}', 400),
            ("POST", "/reservations/0/release", "{}", 400),
            ("POST", "/reservations/999/release", "{}", 404),
            ("POST", "/reservations", '{"idempotency_key":"k","sku":"x","quantity":1}', 409),
            ("POST", "/items", "x" * 65537, 413)]:
            status, error = self.request(address, method, path, body)
            self.assertEqual(status, expected)
            self.assertIsInstance(error["error"], str)
        self.assertEqual(self.request(address, "GET", "/report")[1], self.store.report())

    def test_concurrent_http(self):
        address = self.start_server()
        self.store.add_item("sku", 5)
        def reserve(i):
            return self.request(address, "POST", "/reservations", json.dumps(
                {"idempotency_key": str(i), "sku": "sku", "quantity": 1}))[0]
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            statuses = list(pool.map(reserve, range(15)))
        self.assertEqual(statuses.count(201), 5)
        self.assertEqual(statuses.count(409), 10)
        self.assertEqual(self.store.report()["reserved_units"], 5)

    def test_cli_shared_state_and_errors(self):
        def run(*args):
            return subprocess.run([sys.executable, "-m", "reservation", "--db", self.path, *args],
                                  capture_output=True, text=True)
        result = run("add", "--sku", "sku", "--quantity", "3")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), self.store.get_item("sku"))
        result = run("reserve", "--key", "key", "--sku", "sku", "--quantity", "2")
        record = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.store.reserve("key", "sku", 2), record)
        result = run("release", "--id", str(record["reservation_id"]))
        self.assertEqual(json.loads(result.stdout)["status"], "released")
        self.assertEqual(json.loads(run("report").stdout), self.store.report())
        for args in [("add", "--sku", "sku", "--quantity", "0"),
                     ("add", "--sku", "sku", "--quantity", "bad"),
                     ("release", "--id", "999")]:
            result = run(*args)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIsInstance(json.loads(result.stderr)["error"], str)
