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


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "inventory.db")
        self.store = Store(self.path)

    def test_persistence_retries_and_report(self):
        self.assertEqual(self.store.add_item(" z ", 5), {"sku": "z", "available": 5})
        self.store.add_item("z", 2)
        self.store.add_item("a", 3)
        record = self.store.reserve(" key ", " z ", 4)
        reopened = Store(self.path)
        self.assertEqual(reopened.reserve("key", "z", 4), record)
        self.assertEqual(reopened.report(), {
            "items": [{"sku": "a", "available": 3}, {"sku": "z", "available": 3}],
            "active_reservations": 1, "reserved_units": 4})
        released = reopened.release(record["reservation_id"])
        self.assertEqual(released["status"], "released")
        self.assertEqual(reopened.release(record["reservation_id"]), released)
        self.assertEqual(Store(self.path).reserve("key", "z", 4), released)
        self.assertEqual(reopened.get_item("z")["available"], 7)
        self.assertEqual(reopened.report()["reserved_units"], 0)

    def test_failures_leave_state_unchanged(self):
        self.store.add_item("sku", 2)
        self.store.reserve("used", "sku", 1)
        before = self.store.report()
        for args, error in [(('used', 'sku', 2), Conflict),
                            (('used', 'other', 1), Conflict),
                            (('new', 'sku', 2), Conflict),
                            (('new', 'missing', 1), NotFound)]:
            with self.assertRaises(error):
                self.store.reserve(*args)
            self.assertEqual(self.store.report(), before)
        self.store.add_item("sku", 2)
        self.assertEqual(self.store.reserve("new", "sku", 2)["status"], "active")
        with self.assertRaises(NotFound):
            self.store.get_item("missing")
        with self.assertRaises(NotFound):
            self.store.release(999)

    def test_validation(self):
        for invalid in [None, True, 1, "", " \t"]:
            for call in [lambda: self.store.add_item(invalid, 1),
                         lambda: self.store.get_item(invalid),
                         lambda: self.store.reserve(invalid, "sku", 1),
                         lambda: self.store.reserve("key", invalid, 1)]:
                with self.assertRaises(ValueError):
                    call()
        for invalid in [None, True, False, 0, -1, 1.0, "1"]:
            for call in [lambda: self.store.add_item("sku", invalid),
                         lambda: self.store.reserve("key", "sku", invalid),
                         lambda: self.store.release(invalid)]:
                with self.assertRaises(ValueError):
                    call()
        self.assertEqual(self.store.report()["items"], [])

    def test_large_integer_quantities_remain_exact(self):
        quantity = 2 ** 80
        self.store.add_item("large", quantity)
        self.store.add_item("large", 1)
        record = self.store.reserve("large-key", "large", quantity)
        self.assertEqual(self.store.get_item("large")["available"], 1)
        self.assertEqual(Store(self.path).report()["reserved_units"], quantity)
        self.store.release(record["reservation_id"])
        self.assertEqual(self.store.get_item("large")["available"], quantity + 1)
        with self.assertRaises(NotFound):
            self.store.release(quantity)

    def test_concurrent_stock_retries_and_release(self):
        self.store.add_item("sku", 10)

        def reserve(i):
            try:
                return Store(self.path).reserve(f"key-{i}", "sku", 1)
            except Conflict:
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            records = [r for r in pool.map(reserve, range(30)) if r is not None]
        self.assertEqual(len(records), 10)
        self.assertEqual(self.store.get_item("sku")["available"], 0)
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            releases = list(pool.map(lambda _: Store(self.path).release(records[0]["reservation_id"]), range(24)))
        self.assertTrue(all(r == releases[0] for r in releases))
        self.assertEqual(self.store.get_item("sku")["available"], 1)
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            retries = list(pool.map(lambda _: Store(self.path).reserve("shared", "sku", 1), range(24)))
        self.assertTrue(all(r == retries[0] for r in retries))
        self.assertEqual(self.store.report()["active_reservations"], 10)
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(lambda _: Store(self.path).add_item("sku", 1), range(24)))
        self.assertEqual(self.store.get_item("sku")["available"], 24)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "inventory.db")
        self.server = create_server(self.path)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, method, path, body=None, raw=None, headers=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        try:
            if body is not None:
                raw = json.dumps(body).encode("utf-8")
            connection.request(method, path, body=raw, headers=headers or {})
            response = connection.getresponse()
            payload = response.read()
            self.assertEqual(response.getheader("Content-Type"), "application/json")
            self.assertEqual(int(response.getheader("Content-Length")), len(payload))
            return response.status, json.loads(payload)
        finally:
            connection.close()

    def test_routes_and_shared_state(self):
        self.assertEqual(self.request("GET", "/health"), (200, {"ok": True}))
        sku = "café / widget"
        status, item = self.request("POST", "/items", {"sku": sku, "quantity": 5})
        self.assertEqual(status, 201)
        self.assertEqual(self.request("GET", "/items/" + quote(sku, safe="")), (200, item))
        data = {"idempotency_key": "key", "sku": sku, "quantity": 2}
        status, record = self.request("POST", "/reservations", data)
        self.assertEqual(status, 201)
        self.assertEqual(self.request("POST", "/reservations", data), (201, record))
        route = f'/reservations/{record["reservation_id"]}/release'
        status, released = self.request("POST", route, {})
        self.assertEqual(status, 200)
        self.assertEqual(self.request("POST", route, {}), (200, released))
        self.assertEqual(self.request("POST", "/reservations", data), (201, released))
        self.assertEqual(self.request("GET", "/report"), (200, Store(self.path).report()))

    def test_errors_and_body_limit(self):
        cases = [("GET", "/missing", None, None, 404),
                 ("GET", "/items/missing", None, None, 404),
                 ("POST", "/items", None, b"{", 400),
                 ("POST", "/items", [], None, 400),
                 ("POST", "/items", {}, None, 400),
                 ("POST", "/items", {"sku": "x", "quantity": True}, None, 400),
                 ("POST", "/reservations/0/release", {}, None, 400),
                 ("POST", "/reservations/no/release", {}, None, 400),
                 ("POST", "/reservations/999/release", {}, None, 404),
                 ("POST", "/items", None, b"\xff", 400),
                 ("POST", "/items", None, b'{"sku":"x","quantity":NaN}', 400),
                 ("TRACE", "/health", None, None, 404),
                 ("POST", "/items", None, b" " * 65537, 413)]
        for method, path, body, raw, expected in cases:
            with self.subTest(path=path, raw=raw and raw[:10], body=body):
                status, result = self.request(method, path, body, raw)
                self.assertEqual(status, expected)
                self.assertIsInstance(result["error"], str)
        self.request("POST", "/items", {"sku": "x", "quantity": 1})
        data = {"idempotency_key": "key", "sku": "x", "quantity": 2}
        self.assertEqual(self.request("POST", "/reservations", data)[0], 409)
        self.assertEqual(self.request("GET", "/health")[0], 200)
        self.assertEqual(self.request("POST", "/items", raw=b"",
                                      headers={"Content-Length": "9" * 100})[0], 413)
        self.assertEqual(self.request("POST", "/items", raw=b"",
                                      headers={"Content-Length": "-1"})[0], 400)
        # Exactly 64 KiB is accepted, without requiring a large JSON object.
        raw = b'{"sku":"x","quantity":1}'
        self.assertEqual(self.request("POST", "/items", raw=raw.ljust(65536))[0], 201)

    def test_concurrent_requests(self):
        self.request("POST", "/items", {"sku": "x", "quantity": 5})
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda i: self.request("POST", "/reservations", {
                "idempotency_key": str(i), "sku": "x", "quantity": 1}), range(15)))
        self.assertEqual(sum(status == 201 for status, _ in results), 5)
        self.assertEqual(sum(status == 409 for status, _ in results), 10)
        self.assertEqual(Store(self.path).report()["reserved_units"], 5)


class CLITests(unittest.TestCase):
    def test_cli_state_and_json_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "inventory.db")

            def run(*args):
                return subprocess.run([sys.executable, "-m", "reservation", "--db", path, *args],
                                      capture_output=True, text=True, timeout=10)

            result = run("add", "--sku", "x", "--quantity", "3")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {"sku": "x", "available": 3})
            result = run("reserve", "--key", "key", "--sku", "x", "--quantity", "2")
            self.assertEqual(result.returncode, 0, result.stderr)
            record = json.loads(result.stdout)
            self.assertEqual(Store(path).get_item("x")["available"], 1)
            result = run("release", "--id", str(record["reservation_id"]))
            self.assertEqual(json.loads(result.stdout)["status"], "released")
            self.assertEqual(json.loads(run("report").stdout), Store(path).report())
            for args in [("add", "--sku", "x", "--quantity", "0"),
                         ("add", "--sku", "x", "--quantity", "oops"),
                         ("add",), ("release", "--id", "999"),
                         ("reserve", "--key", "key", "--sku", "x", "--quantity", "1")]:
                result = run(*args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIsInstance(json.loads(result.stderr)["error"], str)


if __name__ == "__main__":
    unittest.main()
