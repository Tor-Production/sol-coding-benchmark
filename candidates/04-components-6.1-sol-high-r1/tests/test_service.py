"""Contract checks across the store, HTTP API, and command-line interface."""

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


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "inventory.db"
        self.store = Store(self.path)


class StoreTests(DatabaseTests):
    def test_persistence_shapes_and_idempotency_after_release(self):
        self.assertEqual(self.store.report(), {
            "items": [], "active_reservations": 0, "reserved_units": 0,
        })
        self.assertEqual(self.store.add_item(" z ", 5), {"sku": "z", "available": 5})
        self.store.add_item("a", 2)
        self.assertEqual(self.store.add_item("z", 3)["available"], 8)
        record = self.store.reserve(" key ", " z ", 3)
        self.assertEqual(record, {
            "reservation_id": record["reservation_id"], "idempotency_key": "key",
            "sku": "z", "quantity": 3, "status": "active",
        })
        self.assertGreater(record["reservation_id"], 0)
        reopened = Store(self.path)
        self.assertEqual(reopened.reserve("key", "z", 3), record)
        self.assertEqual(reopened.report(), {
            "items": [{"sku": "a", "available": 2}, {"sku": "z", "available": 5}],
            "active_reservations": 1, "reserved_units": 3,
        })
        released = reopened.release(record["reservation_id"])
        self.assertEqual(released["status"], "released")
        self.assertEqual(self.store.release(record["reservation_id"]), released)
        self.assertEqual(Store(self.path).reserve("key", "z", 3), released)
        self.assertEqual(self.store.get_item("z")["available"], 8)
        self.assertEqual(self.store.report()["reserved_units"], 0)

    def test_failures_leave_state_unchanged_and_keys_reusable(self):
        self.store.add_item("sku", 3)
        before = self.store.report()
        with self.assertRaises(Conflict):
            self.store.reserve("key", "sku", 4)
        self.assertEqual(self.store.report(), before)
        with self.assertRaises(NotFound):
            self.store.reserve("key", "missing", 1)
        self.assertEqual(self.store.report(), before)
        record = self.store.reserve("key", "sku", 2)
        before = self.store.report()
        for sku, quantity in [("missing", 2), ("sku", 1)]:
            with self.assertRaises(Conflict):
                self.store.reserve("key", sku, quantity)
            self.assertEqual(self.store.report(), before)
        with self.assertRaises(NotFound):
            self.store.release(record["reservation_id"] + 1)
        with self.assertRaises(NotFound):
            self.store.get_item("missing")
        self.assertEqual(self.store.report(), before)

    def test_validation(self):
        for bad in [None, "", " \t ", 1, False, [], {}]:
            with self.subTest(text=bad):
                for call in [lambda: self.store.add_item(bad, 1),
                             lambda: self.store.get_item(bad),
                             lambda: self.store.reserve(bad, "sku", 1),
                             lambda: self.store.reserve("key", bad, 1)]:
                    with self.assertRaises(ValueError):
                        call()
        for bad in [None, 0, -1, True, False, 1.0, "1", [], {}]:
            with self.subTest(integer=bad):
                for call in [lambda: self.store.add_item("sku", bad),
                             lambda: self.store.reserve("key", "sku", bad),
                             lambda: self.store.release(bad)]:
                    with self.assertRaises(ValueError):
                        call()
        self.assertEqual(self.store.report()["items"], [])

    def test_large_positive_integers_preserve_exact_units(self):
        quantity = 10 ** 30
        self.store.add_item("sku", quantity)
        self.assertEqual(self.store.add_item("sku", 1)["available"], quantity + 1)
        record = self.store.reserve("key", "sku", quantity)
        self.assertEqual(Store(self.path).reserve("key", "sku", quantity), record)
        self.assertEqual(self.store.report()["reserved_units"], quantity)
        self.assertEqual(self.store.get_item("sku")["available"], 1)
        self.store.release(record["reservation_id"])
        self.assertEqual(self.store.get_item("sku")["available"], quantity + 1)
        with self.assertRaises(NotFound):
            self.store.release(quantity)

    def test_concurrent_add_reserve_retry_and_release(self):
        def add(_):
            return Store(self.path).add_item("sku", 1)

        with ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(add, range(20)))
        self.assertEqual(self.store.get_item("sku")["available"], 20)

        def reserve(index):
            try:
                return Store(self.path).reserve(f"key-{index}", "sku", 1)
            except Conflict:
                return None

        with ThreadPoolExecutor(max_workers=12) as pool:
            records = [r for r in pool.map(reserve, range(40)) if r is not None]
        self.assertEqual(len(records), 20)
        self.assertEqual(len({r["reservation_id"] for r in records}), 20)
        self.assertEqual(self.store.get_item("sku")["available"], 0)
        self.assertEqual(self.store.report()["reserved_units"], 20)

        record = records[0]
        with ThreadPoolExecutor(max_workers=12) as pool:
            released = list(pool.map(
                lambda _: Store(self.path).release(record["reservation_id"]), range(20)
            ))
        self.assertTrue(all(r == released[0] for r in released))
        self.assertEqual(self.store.get_item("sku")["available"], 1)
        with ThreadPoolExecutor(max_workers=12) as pool:
            retries = list(pool.map(
                lambda _: Store(self.path).reserve("shared", "sku", 1), range(20)
            ))
        self.assertTrue(all(r == retries[0] for r in retries))
        self.assertEqual(self.store.get_item("sku")["available"], 0)
        self.assertEqual(self.store.report()["active_reservations"], 20)


class HTTPTests(DatabaseTests):
    def setUp(self):
        super().setUp()
        self.server = create_server(self.path)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.assertFalse(self.thread.is_alive())

    def request(self, method, path, body=None, raw=None, headers=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        try:
            payload = raw if raw is not None else (
                json.dumps(body).encode("utf-8") if body is not None else None
            )
            connection.request(method, path, payload, headers or {})
            response = connection.getresponse()
            data = response.read()
            self.assertEqual(response.getheader("Content-Type"), "application/json")
            self.assertEqual(int(response.getheader("Content-Length")), len(data))
            record = json.loads(data.decode("utf-8"))
            if response.status >= 400:
                self.assertEqual(set(record), {"error"})
                self.assertIsInstance(record["error"], str)
            return response.status, record
        finally:
            connection.close()

    def test_routes_unicode_and_shared_state(self):
        self.assertEqual(self.request("GET", "/health"), (200, {"ok": True}))
        sku = "café / 茶"
        self.assertEqual(self.request("POST", "/items", {"sku": sku, "quantity": 5}),
                         (201, {"sku": sku, "available": 5}))
        self.assertEqual(self.request("GET", "/items/" + quote(sku, safe="")),
                         (200, {"sku": sku, "available": 5}))
        body = {"idempotency_key": "order", "sku": sku, "quantity": 2}
        status, record = self.request("POST", "/reservations", body)
        self.assertEqual(status, 201)
        self.assertEqual(self.request("POST", "/reservations", body), (201, record))
        self.assertEqual(self.store.get_item(sku)["available"], 3)
        route = f"/reservations/{record['reservation_id']}/release"
        status, released = self.request("POST", route, {})
        self.assertEqual(status, 200)
        self.assertEqual(released["status"], "released")
        self.assertEqual(self.request("POST", route, {}), (200, released))
        self.assertEqual(self.request("GET", "/report"), (200, self.store.report()))

    def test_errors_and_server_survival(self):
        for raw in [b"", b"{", b"[]", b"null", b'"text"', b"\xff", b"{}",
                    b'{"sku":"sku","quantity":1,"extra":NaN}',
                    b'{"sku":"sku","quantity":1,"extra":Infinity}']:
            with self.subTest(raw=raw):
                self.assertEqual(self.request("POST", "/items", raw=raw)[0], 400)
        for quantity in [0, -1, True, 1.5, "1", None]:
            self.assertEqual(self.request("POST", "/items", {
                "sku": "sku", "quantity": quantity,
            })[0], 400)
        self.assertEqual(self.request("GET", "/unknown")[0], 404)
        self.assertEqual(self.request("POST", "/unknown", {})[0], 404)
        self.assertEqual(self.request("PUT", "/items", {})[0], 404)
        self.assertEqual(self.request("CUSTOM", "/items", {})[0], 404)
        self.assertEqual(self.request("GET", "/items/missing")[0], 404)
        self.assertEqual(self.request("POST", "/reservations/99/release", {})[0], 404)
        self.assertEqual(self.request("POST", "/reservations/abc/release", {})[0], 400)
        self.assertEqual(self.request("POST", "/reservations/0/release", {})[0], 400)
        self.assertEqual(self.request("POST", "/items", raw=b"{}",
                                      headers={"Content-Length": "-1"})[0], 400)
        # The server must reject the header without waiting for the declared body.
        self.assertEqual(self.request("POST", "/items", raw=b"{}",
                                      headers={"Content-Length": "65537"})[0], 413)
        self.assertEqual(self.request("POST", "/items", raw=b"x" * 65536)[0], 400)
        self.store.add_item("sku", 1)
        body = {"idempotency_key": "key", "sku": "sku", "quantity": 2}
        self.assertEqual(self.request("POST", "/reservations", body)[0], 409)
        body["sku"] = "missing"
        self.assertEqual(self.request("POST", "/reservations", body)[0], 404)
        self.assertEqual(self.request("GET", "/health"), (200, {"ok": True}))

    def test_concurrent_requests(self):
        self.store.add_item("sku", 5)

        def reserve(index):
            return self.request("POST", "/reservations", {
                "idempotency_key": f"key-{index}", "sku": "sku", "quantity": 1,
            })[0]

        with ThreadPoolExecutor(max_workers=8) as pool:
            statuses = list(pool.map(reserve, range(16)))
        self.assertEqual(statuses.count(201), 5)
        self.assertEqual(statuses.count(409), 11)
        self.assertEqual(self.store.report(), {
            "items": [{"sku": "sku", "available": 0}],
            "active_reservations": 5, "reserved_units": 5,
        })


class CLITests(DatabaseTests):
    def cli(self, *arguments):
        return subprocess.run(
            [sys.executable, "-m", "reservation", "--db", str(self.path), *arguments],
            capture_output=True, text=True, timeout=10,
        )

    def success(self, *arguments):
        result = self.cli(*arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(result.stdout.splitlines()), 1)
        return json.loads(result.stdout)

    def test_success_and_shared_state(self):
        self.assertEqual(self.success("add", "--sku", "sku", "--quantity", "3"),
                         {"sku": "sku", "available": 3})
        args = ("reserve", "--key", "key", "--sku", "sku", "--quantity", "2")
        record = self.success(*args)
        self.assertEqual(self.success(*args), record)
        self.assertEqual(self.store.get_item("sku")["available"], 1)
        self.assertEqual(self.success("report"), self.store.report())
        self.assertEqual(self.success("release", "--id", str(record["reservation_id"]))
                         ["status"], "released")
        self.assertEqual(self.store.get_item("sku")["available"], 3)

    def test_errors(self):
        for arguments in [(), ("unknown",), ("add",),
                          ("add", "--sku", "sku", "--quantity", "abc"),
                          ("add", "--sku", " ", "--quantity", "1"),
                          ("add", "--sku", "sku", "--quantity", "0"),
                          ("reserve", "--key", "key", "--sku", "missing", "--quantity", "1"),
                          ("release", "--id", "1"), ("release", "--id", "-1"),
                          ("serve", "--port", "65536")]:
            with self.subTest(arguments=arguments):
                result = self.cli(*arguments)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertEqual(set(json.loads(result.stderr)), {"error"})
        self.store.add_item("sku", 1)
        result = self.cli("reserve", "--key", "key", "--sku", "sku", "--quantity", "2")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(set(json.loads(result.stderr)), {"error"})


if __name__ == "__main__":
    unittest.main()
