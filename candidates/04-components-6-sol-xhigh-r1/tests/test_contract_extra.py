"""Contract checks that use database files in the workspace root."""

import http.client
import json
import subprocess
import sys
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Thread
from urllib.parse import quote

from reservation.http_api import create_server
from reservation.store import Conflict, NotFound, Store


class WorkspaceDatabaseTest(unittest.TestCase):
    def setUp(self):
        self.db = Path(__file__).resolve().parent.parent / f"test-{uuid.uuid4().hex}.db"
        self.addCleanup(self.db.unlink, missing_ok=True)


class StoreContractTests(WorkspaceDatabaseTest):
    def test_persistence_idempotency_and_report(self):
        store = Store(self.db)
        self.assertEqual(store.add_item(" z ", 4), {"sku": "z", "available": 4})
        self.assertEqual(store.add_item("a", 3), {"sku": "a", "available": 3})
        original = store.reserve(" key ", "z", 2)
        self.assertEqual(Store(self.db).reserve("key", " z ", 2), original)
        self.assertEqual(store.report(), {
            "items": [{"sku": "a", "available": 3}, {"sku": "z", "available": 2}],
            "active_reservations": 1,
            "reserved_units": 2,
        })
        with self.assertRaises(Conflict):
            store.reserve("key", "z", 1)
        released = Store(self.db).release(original["reservation_id"])
        self.assertEqual(released["status"], "released")
        self.assertEqual(store.release(original["reservation_id"]), released)
        self.assertEqual(store.reserve("key", "z", 2), original)
        self.assertEqual(Store(self.db).get_item("z")["available"], 4)

    def test_validation_and_failed_reservation(self):
        store = Store(self.db)
        for bad in (0, -1, True, 1.5, "1", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                store.add_item("sku", bad)
        with self.assertRaises(ValueError):
            store.reserve("  ", "sku", 1)
        with self.assertRaises(ValueError):
            store.get_item(None)
        with self.assertRaises(ValueError):
            store.release(True)
        with self.assertRaises(NotFound):
            store.reserve("missing", "sku", 1)
        store.add_item("sku", 1)
        with self.assertRaises(Conflict):
            store.reserve("too-many", "sku", 2)
        self.assertEqual(store.report()["active_reservations"], 0)
        self.assertEqual(store.get_item("sku")["available"], 1)
        with self.assertRaises(NotFound):
            store.release(2**70)

    def test_concurrent_store_instances_do_not_oversell_or_duplicate(self):
        Store(self.db).add_item("sku", 7)

        def attempt(index):
            try:
                return Store(self.db).reserve(f"key-{index}", "sku", 1)
            except Conflict:
                return None

        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(attempt, range(20)))
        self.assertEqual(sum(result is not None for result in results), 7)
        self.assertEqual(Store(self.db).get_item("sku")["available"], 0)

        Store(self.db).add_item("sku", 1)
        with ThreadPoolExecutor(max_workers=12) as pool:
            retries = list(pool.map(lambda _: Store(self.db).reserve("shared", "sku", 1), range(12)))
        self.assertEqual(len({record["reservation_id"] for record in retries}), 1)
        self.assertEqual(Store(self.db).get_item("sku")["available"], 0)
        reservation_id = retries[0]["reservation_id"]
        with ThreadPoolExecutor(max_workers=12) as pool:
            releases = list(pool.map(lambda _: Store(self.db).release(reservation_id), range(12)))
        self.assertTrue(all(record["status"] == "released" for record in releases))
        self.assertEqual(Store(self.db).get_item("sku")["available"], 1)


class HttpContractTests(WorkspaceDatabaseTest):
    def setUp(self):
        super().setUp()
        self.server = create_server(self.db)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)

    def _stop_server(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()

    def request(self, method, path, payload=None, headers=None):
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            raw = response.read()
            self.assertEqual(response.getheader("Content-Type"), "application/json")
            self.assertEqual(int(response.getheader("Content-Length")), len(raw))
            return response.status, json.loads(raw)
        finally:
            connection.close()

    def test_routes_and_errors(self):
        self.assertEqual(self.request("GET", "/health"), (200, {"ok": True}))
        sku = "snow ☃"
        self.assertEqual(self.request("POST", "/items", {"sku": sku, "quantity": 3}),
                         (201, {"sku": sku, "available": 3}))
        self.assertEqual(self.request("GET", "/items/" + quote(sku)),
                         (200, {"sku": sku, "available": 3}))
        payload = {"idempotency_key": "k", "sku": sku, "quantity": 2}
        status, reservation = self.request("POST", "/reservations", payload)
        self.assertEqual(status, 201)
        self.assertEqual(self.request("POST", "/reservations", payload),
                         (201, reservation))
        path = f"/reservations/{reservation['reservation_id']}/release"
        self.assertEqual(self.request("POST", path, {})[1]["status"], "released")
        self.assertEqual(self.request("POST", "/reservations", payload),
                         (201, reservation))
        self.assertEqual(self.request("GET", "/report")[1]["reserved_units"], 0)
        for method, path, payload, expected in (
            ("GET", "/unknown", None, 404),
            ("GET", "/items/missing", None, 404),
            ("PUT", "/items", None, 404),
            ("POST", "/items", {"sku": "x"}, 400),
            ("POST", "/items", [1, 2], 400),
            ("POST", "/items", {"sku": "x", "quantity": True}, 400),
            ("POST", "/reservations", {**payload, "quantity": 3}, 409),
        ):
            with self.subTest(path=path, expected=expected):
                status, result = self.request(method, path, payload)
                self.assertEqual(status, expected)
                self.assertIsInstance(result["error"], str)

    def test_body_size_and_malformed_json(self):
        status, result = self.request("POST", "/items", None,
                                      {"Content-Length": str(65537)})
        self.assertEqual(status, 413)
        self.assertIn("error", result)
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        try:
            connection.request("POST", "/items", body=b"{bad")
            response = connection.getresponse()
            self.assertEqual(response.status, 400)
            self.assertIn("error", json.loads(response.read()))
        finally:
            connection.close()
        self.assertEqual(self.request("GET", "/health")[0], 200)

    def test_concurrent_requests_respect_stock(self):
        self.request("POST", "/items", {"sku": "sku", "quantity": 5})

        def attempt(index):
            return self.request("POST", "/reservations", {
                "idempotency_key": f"http-{index}", "sku": "sku", "quantity": 1,
            })[0]

        with ThreadPoolExecutor(max_workers=10) as pool:
            statuses = list(pool.map(attempt, range(12)))
        self.assertEqual(statuses.count(201), 5)
        self.assertEqual(statuses.count(409), 7)
        self.assertEqual(self.request("GET", "/items/sku")[1]["available"], 0)


class CliContractTests(WorkspaceDatabaseTest):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "reservation", "--db", str(self.db), *args],
            capture_output=True, text=True, check=False,
        )

    def test_cli_shares_database_and_reports_errors(self):
        added = self.run_cli("add", "--sku", "s", "--quantity", "2")
        self.assertEqual(added.returncode, 0)
        self.assertEqual(json.loads(added.stdout), {"sku": "s", "available": 2})
        reserved = self.run_cli("reserve", "--key", "k", "--sku", "s", "--quantity", "1")
        self.assertEqual(reserved.returncode, 0)
        record = json.loads(reserved.stdout)
        self.assertEqual(Store(self.db).get_item("s")["available"], 1)
        self.assertEqual(self.run_cli("release", "--id", str(record["reservation_id"])).returncode, 0)
        invalid = self.run_cli("add", "--sku", "s", "--quantity", "0")
        self.assertEqual(invalid.returncode, 2)
        self.assertFalse(invalid.stdout)
        self.assertIn("error", json.loads(invalid.stderr))


if __name__ == "__main__":
    unittest.main()
