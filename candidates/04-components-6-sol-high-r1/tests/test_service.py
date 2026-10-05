"""Integration checks that use workspace files for sandbox compatibility."""

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
from reservation.store import Conflict, Store


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.db = Path.cwd() / f".service-test-{uuid4().hex}.db"
        self.addCleanup(self._cleanup_db)

    def _cleanup_db(self):
        for suffix in ("", "-journal", "-wal", "-shm"):
            self.db.with_name(self.db.name + suffix).unlink(missing_ok=True)

    def test_persistence_idempotency_and_report(self):
        store = Store(self.db)
        self.assertEqual(store.add_item(" b ", 5), {"sku": "b", "available": 5})
        self.assertEqual(store.add_item("a", 1), {"sku": "a", "available": 1})
        original = store.reserve(" key ", "b", 3)
        reopened = Store(self.db)
        self.assertEqual(reopened.reserve("key", " b ", 3), original)
        with self.assertRaises(Conflict):
            reopened.reserve("key", "b", 2)
        self.assertEqual(reopened.report(), {
            "items": [{"sku": "a", "available": 1}, {"sku": "b", "available": 2}],
            "active_reservations": 1,
            "reserved_units": 3,
        })
        released = reopened.release(original["reservation_id"])
        self.assertEqual(released["status"], "released")
        self.assertEqual(store.release(original["reservation_id"]), released)
        self.assertEqual(store.reserve("key", "b", 3), original)
        self.assertEqual(store.get_item("b")["available"], 5)

    def test_concurrent_instances_do_not_oversell(self):
        Store(self.db).add_item("sku", 10)

        def attempt(number):
            try:
                return Store(self.db).reserve(f"key-{number}", "sku", 1)
            except Conflict:
                return None

        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(attempt, range(30)))
        successes = [result for result in results if result is not None]
        self.assertEqual(len(successes), 10)
        self.assertEqual(len({result["reservation_id"] for result in successes}), 10)
        self.assertEqual(Store(self.db).report()["reserved_units"], 10)
        self.assertEqual(Store(self.db).get_item("sku")["available"], 0)

    def test_http_and_cli_share_state(self):
        server = create_server(self.db)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def request(method, path, value=None, headers=None):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
            data = None if value is None else json.dumps(value).encode("utf-8")
            connection.request(method, path, body=data, headers=headers or {})
            response = connection.getresponse()
            raw = response.read()
            result = response.status, json.loads(raw), response.getheader("Content-Length")
            self.assertEqual(response.getheader("Content-Type"), "application/json")
            self.assertEqual(int(result[2]), len(raw))
            connection.close()
            return result[:2]

        self.assertEqual(request("GET", "/health"), (200, {"ok": True}))
        self.assertEqual(request("POST", "/items", {"sku": "ü/a", "quantity": 2}),
                         (201, {"sku": "ü/a", "available": 2}))
        self.assertEqual(request("GET", "/items/" + quote("ü/a", safe="")),
                         (200, {"sku": "ü/a", "available": 2}))
        status, reservation = request("POST", "/reservations", {
            "idempotency_key": "one", "sku": "ü/a", "quantity": 2,
        })
        self.assertEqual(status, 201)
        self.assertEqual(request("POST", "/reservations/1/release", {}),
                         (200, {**reservation, "status": "released"}))
        self.assertEqual(request("POST", "/reservations", {
            "idempotency_key": "one", "sku": "ü/a", "quantity": 2,
        }), (201, reservation))
        self.assertEqual(request("POST", "/items", {"sku": "x", "quantity": True})[0], 400)
        self.assertEqual(request("POST", "/items", {"sku": "x", "quantity": 1},
                                 {"Content-Length": "65537"})[0], 413)
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        connection.request("POST", "/items", body=b"{")
        response = connection.getresponse()
        self.assertEqual(response.status, 400)
        self.assertIn("error", json.loads(response.read()))
        connection.close()
        self.assertEqual(request("GET", "/health"), (200, {"ok": True}))
        self.assertEqual(request("GET", "/missing")[0], 404)

        completed = subprocess.run(
            [sys.executable, "-m", "reservation", "--db", str(self.db), "report"],
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(completed.stdout)["items"],
                         [{"sku": "ü/a", "available": 2}])
        invalid = subprocess.run(
            [sys.executable, "-m", "reservation", "--db", str(self.db),
             "reserve", "--key", "other", "--sku", "ü/a", "--quantity", "0"],
            capture_output=True, text=True,
        )
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(invalid.stdout, "")
        self.assertIn("error", json.loads(invalid.stderr))
