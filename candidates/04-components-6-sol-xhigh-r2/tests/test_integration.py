"""Cross-interface checks for durable, concurrent reservations."""

import http.client
import json
import subprocess
import sys
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from reservation.http_api import create_server
from reservation.store import Conflict, NotFound, Store


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.db_path = Path.cwd() / f".reservation-test-{uuid.uuid4().hex}.db"
        self.addCleanup(self.db_path.unlink, missing_ok=True)

    def test_store_idempotency_release_and_report(self):
        store = Store(self.db_path)
        self.assertEqual(store.add_item(" z ", 4), {"sku": "z", "available": 4})
        store.add_item("a", 1)
        first = store.reserve(" key ", " z ", 2)
        self.assertEqual(Store(self.db_path).reserve("key", "z", 2), first)
        with self.assertRaises(Conflict):
            store.reserve("key", "z", 1)
        released = store.release(first["reservation_id"])
        self.assertEqual(released["status"], "released")
        self.assertEqual(store.release(first["reservation_id"]), released)
        self.assertEqual(Store(self.db_path).reserve("key", "z", 2), first)
        self.assertEqual(
            store.report(),
            {
                "items": [{"sku": "a", "available": 1}, {"sku": "z", "available": 4}],
                "active_reservations": 0,
                "reserved_units": 0,
            },
        )
        with self.assertRaises(NotFound):
            store.release(100000)
        for invalid in (True, 0, -1, 1.5, "2"):
            with self.assertRaises(ValueError):
                store.add_item("z", invalid)

    def test_in_memory_store_retains_state_between_calls(self):
        store = Store(":memory:")
        store.add_item("sku", 2)
        self.assertEqual(store.reserve("key", "sku", 1)["quantity"], 1)
        self.assertEqual(store.get_item("sku")["available"], 1)

    def test_quantities_exceeding_sqlite_integer_range(self):
        large = 2**70
        store = Store(self.db_path)
        store.add_item("sku", large)
        reservation = store.reserve("large", "sku", large)
        self.assertEqual(store.report()["reserved_units"], large)
        store.release(reservation["reservation_id"])
        self.assertEqual(Store(self.db_path).get_item("sku")["available"], large)

    def test_separate_stores_never_oversell(self):
        Store(self.db_path).add_item("sku", 5)

        def reserve(number):
            try:
                return Store(self.db_path).reserve(f"key-{number}", "sku", 1)
            except Conflict:
                return None

        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(reserve, range(20)))
        winners = [result for result in results if result is not None]
        self.assertEqual(len(winners), 5)
        self.assertEqual(len({result["reservation_id"] for result in winners}), 5)
        self.assertEqual(Store(self.db_path).report()["reserved_units"], 5)
        self.assertEqual(Store(self.db_path).get_item("sku")["available"], 0)

    def test_http_and_cli_share_state(self):
        server = create_server(self.db_path)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def request(method, path, body=None, headers=None):
            conn = http.client.HTTPConnection(*server.server_address, timeout=5)
            if body is not None and not isinstance(body, bytes):
                body = json.dumps(body).encode("utf-8")
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            raw = response.read()
            self.assertEqual(response.getheader("Content-Type"), "application/json")
            self.assertEqual(int(response.getheader("Content-Length")), len(raw))
            result = response.status, json.loads(raw)
            conn.close()
            return result

        self.assertEqual(request("GET", "/health"), (200, {"ok": True}))
        self.assertEqual(
            request("POST", "/items", {"sku": "a/b", "quantity": 2}),
            (201, {"sku": "a/b", "available": 2}),
        )
        self.assertEqual(
            request("GET", "/items/a%2Fb"),
            (200, {"sku": "a/b", "available": 2}),
        )
        self.assertEqual(request("GET", "/items/a/b")[0], 404)
        first_status, first = request(
            "POST", "/reservations", {"idempotency_key": "k", "sku": "a/b", "quantity": 1}
        )
        self.assertEqual(first_status, 201)
        self.assertEqual(
            request("POST", f"/reservations/{first['reservation_id']}/release", {})[0],
            200,
        )
        self.assertEqual(
            request("POST", "/reservations", {"idempotency_key": "k", "sku": "a/b", "quantity": 1}),
            (201, first),
        )
        self.assertEqual(request("POST", "/items", b"[1, 2]")[0], 400)
        self.assertEqual(request("POST", "/items", b"{")[0], 400)
        self.assertEqual(request("POST", "/items", b"x" * 65537)[0], 413)
        self.assertEqual(
            request("GET", "/missing", headers={"Content-Length": "65537"})[0],
            413,
        )
        self.assertEqual(request("GET", "/report")[0], 200)

        completed = subprocess.run(
            [sys.executable, "-m", "reservation", "--db", str(self.db_path), "report"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["items"], [{"sku": "a/b", "available": 2}])
