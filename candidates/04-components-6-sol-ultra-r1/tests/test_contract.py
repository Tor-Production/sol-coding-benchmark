"""Focused end-to-end checks using database files inside this workspace."""

from concurrent.futures import ThreadPoolExecutor
import http.client
import json
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from urllib.parse import quote
from uuid import uuid4

from reservation.http_api import create_server
from reservation.store import Conflict, NotFound, Store


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.db = Path(__file__).resolve().parent / f".contract-{uuid4().hex}.db"
        self.addCleanup(self._remove_db)

    def _remove_db(self):
        for suffix in ("", "-journal", "-wal", "-shm"):
            self.db.with_name(self.db.name + suffix).unlink(missing_ok=True)

    def test_persistence_idempotency_and_release(self):
        store = Store(self.db)
        self.assertEqual(store.add_item("  a  ", 5), {"sku": "a", "available": 5})
        first = store.reserve("  key  ", " a ", 2)
        reopened = Store(self.db)
        self.assertEqual(reopened.reserve("key", "a", 2), first)
        self.assertEqual(reopened.get_item("a")["available"], 3)
        with self.assertRaises(Conflict):
            reopened.reserve("key", "a", 1)
        self.assertEqual(reopened.report()["reserved_units"], 2)
        released = reopened.release(first["reservation_id"])
        self.assertEqual(released["status"], "released")
        self.assertEqual(store.release(first["reservation_id"]), released)
        self.assertEqual(store.reserve("key", "a", 2), released)
        self.assertEqual(store.report(), {
            "items": [{"sku": "a", "available": 5}],
            "active_reservations": 0,
            "reserved_units": 0,
        })

    def test_failures_and_argument_validation(self):
        store = Store(self.db)
        store.add_item("a", 1)
        with self.assertRaises(NotFound):
            store.reserve("missing", "b", 1)
        with self.assertRaises(Conflict):
            store.reserve("too-many", "a", 2)
        self.assertEqual(store.report()["active_reservations"], 0)
        self.assertEqual(store.get_item("a")["available"], 1)
        for invalid in (True, 0, -1, 1.0, "1"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                store.add_item("a", invalid)
        with self.assertRaises(ValueError):
            store.get_item(" ")
        with self.assertRaises(ValueError):
            store.reserve(" ", "a", 1)
        with self.assertRaises(ValueError):
            store.release(True)
        with self.assertRaises(NotFound):
            store.release(2**100)

    def test_concurrent_stores_do_not_oversell_or_duplicate(self):
        Store(self.db).add_item("a", 10)

        def reserve_one(index):
            try:
                return Store(self.db).reserve(f"key-{index}", "a", 1)
            except Conflict:
                return None

        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(reserve_one, range(30)))
        successes = [record for record in results if record is not None]
        self.assertEqual(len(successes), 10)
        self.assertEqual(len({r["reservation_id"] for r in successes}), 10)
        self.assertEqual(Store(self.db).report()["reserved_units"], 10)

        winning_key = successes[0]["idempotency_key"]

        def retry_same_key(_):
            return Store(self.db).reserve(winning_key, "a", 1)

        with ThreadPoolExecutor(max_workers=12) as pool:
            retries = list(pool.map(retry_same_key, range(20)))
        self.assertTrue(all(record == retries[0] for record in retries))
        self.assertEqual(Store(self.db).get_item("a")["available"], 0)

    def test_http_json_routes_and_errors(self):
        server = create_server(self.db)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def request(method, path, body=None, headers=None):
            connection = http.client.HTTPConnection(*server.server_address, timeout=5)
            try:
                if isinstance(body, dict):
                    body = json.dumps(body).encode("utf-8")
                connection.request(method, path, body=body, headers=headers or {})
                response = connection.getresponse()
                raw = response.read()
                self.assertEqual(response.getheader("Content-Type"), "application/json")
                self.assertEqual(int(response.getheader("Content-Length")), len(raw))
                return response.status, json.loads(raw)
            finally:
                connection.close()

        sku = "caf\u00e9/\u03b2"
        self.assertEqual(request("GET", "/health"), (200, {"ok": True}))
        self.assertEqual(request("POST", "/items", {"sku": sku, "quantity": 2}),
                         (201, {"sku": sku, "available": 2}))
        self.assertEqual(request("GET", "/items/" + quote(sku, safe="")),
                         (200, {"sku": sku, "available": 2}))
        status, reservation = request("POST", "/reservations", {
            "idempotency_key": "k", "sku": sku, "quantity": 2
        })
        self.assertEqual(status, 201)
        self.assertEqual(request("POST", "/reservations", {
            "idempotency_key": "k", "sku": sku, "quantity": 2
        }), (201, reservation))
        self.assertEqual(request("POST", f"/reservations/{reservation['reservation_id']}/release", {}),
                         (200, {**reservation, "status": "released"}))
        self.assertEqual(request("POST", "/items", b"[1]" )[0], 400)
        self.assertEqual(request("POST", "/items", b"{" )[0], 400)
        self.assertEqual(request("POST", "/items", {"sku": "x", "quantity": True})[0], 400)
        self.assertEqual(request("GET", "/missing")[0], 404)
        self.assertEqual(request("POST", "/items", b"", {"Content-Length": "65537"})[0], 413)
        self.assertEqual(request("GET", "/health"), (200, {"ok": True}))

    def test_cli_uses_same_database(self):
        root = Path(__file__).resolve().parent.parent

        def command(*args):
            return subprocess.run(
                [sys.executable, "-m", "reservation", "--db", str(self.db), *args],
                cwd=root, capture_output=True, text=True, check=False,
            )

        added = command("add", "--sku", "a", "--quantity", "3")
        self.assertEqual(added.returncode, 0)
        self.assertEqual(json.loads(added.stdout), {"sku": "a", "available": 3})
        self.assertEqual(Store(self.db).get_item("a")["available"], 3)
        invalid = command("reserve", "--key", "k", "--sku", "a", "--quantity", "4")
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(invalid.stdout, "")
        self.assertIn("error", json.loads(invalid.stderr))
