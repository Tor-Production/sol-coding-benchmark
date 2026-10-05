import http.client
import json
import subprocess
import sys
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

from reservation.http_api import create_server
from reservation.store import Conflict, NotFound, Store


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.db = Path.cwd() / f".reservation-test-{uuid.uuid4().hex}.db"
        self.addCleanup(self.db.unlink, missing_ok=True)

    def test_persistence_retry_release_and_report(self):
        first_store = Store(self.db)
        self.assertEqual(first_store.add_item(" b ", 5), {"sku": "b", "available": 5})
        first_store.add_item("a", 2)
        reservation = first_store.reserve(" key ", " b ", 3)
        self.assertEqual(reservation["status"], "active")
        self.assertEqual(reservation["idempotency_key"], "key")

        reopened = Store(self.db)
        self.assertEqual(reopened.get_item("b"), {"sku": "b", "available": 2})
        self.assertEqual(reopened.reserve("key", "b", 3), reservation)
        self.assertEqual(
            reopened.report(),
            {
                "items": [
                    {"sku": "a", "available": 2},
                    {"sku": "b", "available": 2},
                ],
                "active_reservations": 1,
                "reserved_units": 3,
            },
        )
        released = reopened.release(reservation["reservation_id"])
        self.assertEqual(released["status"], "released")
        self.assertEqual(first_store.release(reservation["reservation_id"]), released)
        self.assertEqual(reopened.reserve("key", "b", 3), reservation)
        self.assertEqual(first_store.get_item("b")["available"], 5)
        self.assertEqual(first_store.report()["active_reservations"], 0)

    def test_validation_and_failed_reservations_leave_stock_unchanged(self):
        store = Store(self.db)
        store.add_item("sku", 2)
        for value in (True, 0, -1, 1.0, "1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                store.reserve("key", "sku", value)
        with self.assertRaises(ValueError):
            store.add_item("  ", 1)
        with self.assertRaises(NotFound):
            store.reserve("missing", "other", 1)
        with self.assertRaises(Conflict):
            store.reserve("too-many", "sku", 3)
        self.assertEqual(store.get_item("sku")["available"], 2)
        self.assertEqual(store.report()["active_reservations"], 0)
        reservation = store.reserve("key", "sku", 1)
        with self.assertRaises(Conflict):
            store.reserve("key", "sku", 2)
        self.assertEqual(store.get_item("sku")["available"], 1)
        with self.assertRaises(ValueError):
            store.release(True)
        with self.assertRaises(NotFound):
            store.release(reservation["reservation_id"] + 1)

    def test_concurrent_stores_never_oversell_or_duplicate_key(self):
        left, right = Store(self.db), Store(self.db)
        left.add_item("sku", 12)

        def reserve(index):
            store = left if index % 2 else right
            try:
                return store.reserve(f"key-{index}", "sku", 1)
            except Conflict:
                return None

        with ThreadPoolExecutor(max_workers=16) as workers:
            results = list(workers.map(reserve, range(30)))
        successful = [record for record in results if record is not None]
        self.assertEqual(len(successful), 12)
        self.assertEqual(len({r["reservation_id"] for r in successful}), 12)
        self.assertEqual(right.get_item("sku")["available"], 0)

        used_key = successful[0]["idempotency_key"]
        with ThreadPoolExecutor(max_workers=8) as workers:
            retries = list(
                workers.map(lambda _: left.reserve(used_key, "sku", 1), range(8))
            )
        self.assertTrue(all(record == retries[0] for record in retries))
        self.assertEqual(right.get_item("sku")["available"], 0)

    def test_http_json_routes_and_errors(self):
        server = create_server(self.db)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def request(method, path, body=None, headers=None):
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.server_address[1], timeout=5
            )
            try:
                connection.request(method, path, body=body, headers=headers or {})
                response = connection.getresponse()
                data = response.read()
                self.assertEqual(response.getheader("Content-Type"), "application/json")
                self.assertEqual(int(response.getheader("Content-Length")), len(data))
                return response.status, json.loads(data)
            finally:
                connection.close()

        sku = "snow ☃"
        item_body = json.dumps({"sku": sku, "quantity": 3}).encode("utf-8")
        self.assertEqual(request("GET", "/health"), (200, {"ok": True}))
        self.assertEqual(
            request("POST", "/items", item_body),
            (201, {"sku": sku, "available": 3}),
        )
        self.assertEqual(
            request("GET", "/items/" + quote(sku, safe="")),
            (200, {"sku": sku, "available": 3}),
        )
        reserve_body = json.dumps(
            {"idempotency_key": "k", "sku": sku, "quantity": 2}
        ).encode("utf-8")
        status, reservation = request("POST", "/reservations", reserve_body)
        self.assertEqual(status, 201)
        self.assertEqual(request("POST", "/reservations", reserve_body), (201, reservation))
        self.assertEqual(
            request("POST", f"/reservations/{reservation['reservation_id']}/release", b"{}")
            [1]["status"],
            "released",
        )
        self.assertEqual(request("POST", "/reservations", reserve_body), (201, reservation))
        self.assertEqual(request("POST", "/items", b"[]")[0], 400)
        self.assertEqual(request("POST", "/items", b"{")[0], 400)
        self.assertEqual(request("POST", "/items", b"{}")[0], 400)
        self.assertEqual(request("GET", "/missing")[0], 404)
        self.assertEqual(
            request("POST", "/items", b"", {"Content-Length": "65537"})[0],
            413,
        )
        self.assertEqual(request("GET", "/report")[1]["reserved_units"], 0)

    def test_cli_shares_database_and_reports_json_errors(self):
        def run(*arguments):
            return subprocess.run(
                [sys.executable, "-m", "reservation", "--db", str(self.db), *arguments],
                capture_output=True,
                text=True,
                check=False,
            )

        added = run("add", "--sku", "sku", "--quantity", "2")
        self.assertEqual(added.returncode, 0)
        self.assertEqual(json.loads(added.stdout), {"sku": "sku", "available": 2})
        reserved = run("reserve", "--key", "key", "--sku", "sku", "--quantity", "1")
        self.assertEqual(reserved.returncode, 0)
        self.assertEqual(Store(self.db).get_item("sku")["available"], 1)
        failed = run("release", "--id", "0")
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))
