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


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.db_path = Path.cwd() / f".service-test-{uuid.uuid4().hex}.db"
        self.addCleanup(self.db_path.unlink, missing_ok=True)

    def test_store_persistence_idempotency_and_report(self):
        store = Store(self.db_path)
        self.assertEqual(store.add_item(" z ", 4), {"sku": "z", "available": 4})
        self.assertEqual(store.add_item("a", 2), {"sku": "a", "available": 2})
        original = store.reserve(" key ", "z", 3)
        self.assertEqual(Store(self.db_path).reserve("key", "z", 3), original)
        self.assertEqual(store.release(original["reservation_id"])["status"], "released")
        self.assertEqual(store.release(original["reservation_id"])["status"], "released")
        self.assertEqual(Store(self.db_path).reserve("key", "z", 3), original)
        with self.assertRaises(Conflict):
            store.reserve("key", "z", 2)
        with self.assertRaises(NotFound):
            store.reserve("other", "missing", 1)
        self.assertEqual(
            Store(self.db_path).report(),
            {"items": [{"sku": "a", "available": 2}, {"sku": "z", "available": 4}],
             "active_reservations": 0, "reserved_units": 0},
        )

    def test_validation_and_concurrent_reservations(self):
        Store(self.db_path).add_item("sku", 8)
        with self.assertRaises(ValueError):
            Store(self.db_path).add_item("sku", True)
        with self.assertRaises(ValueError):
            Store(self.db_path).release(False)
        with ThreadPoolExecutor(max_workers=16) as pool:
            outcomes = list(pool.map(self._try_reserve, range(20)))
        self.assertEqual(outcomes.count("active"), 8)
        self.assertEqual(outcomes.count("conflict"), 12)
        report = Store(self.db_path).report()
        self.assertEqual(report["items"], [{"sku": "sku", "available": 0}])
        self.assertEqual(report["active_reservations"], 8)
        self.assertEqual(report["reserved_units"], 8)

    def _try_reserve(self, number):
        try:
            return Store(self.db_path).reserve(f"key-{number}", "sku", 1)["status"]
        except Conflict:
            return "conflict"

    def test_http_and_cli_share_state(self):
        command = [sys.executable, "-m", "reservation", "--db", str(self.db_path)]
        added = subprocess.run(
            command + ["add", "--sku", "sp ace", "--quantity", "3"],
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(added.stdout), {"sku": "sp ace", "available": 3})
        invalid = subprocess.run(
            command + ["add", "--sku", "sp ace", "--quantity", "0"],
            capture_output=True, text=True,
        )
        self.assertEqual(invalid.returncode, 2)
        self.assertIn("error", json.loads(invalid.stderr))
        server = create_server(self.db_path)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self.assertEqual(self._request(server, "GET", "/health"), (200, {"ok": True}))
            self.assertEqual(
                self._request(server, "GET", "/items/sp%20ace"),
                (200, {"sku": "sp ace", "available": 3}),
            )
            status, reservation = self._request(
                server, "POST", "/reservations",
                {"idempotency_key": "k", "sku": "sp ace", "quantity": 2},
            )
            self.assertEqual(status, 201)
            self.assertEqual(
                self._request(server, "POST", "/reservations",
                              {"idempotency_key": "k", "sku": "sp ace", "quantity": 2}),
                (201, reservation),
            )
            self.assertEqual(
                self._request(server, "POST", "/reservations",
                              {"idempotency_key": "different", "sku": "sp ace", "quantity": 2})[0],
                409,
            )
            self.assertEqual(self._request(server, "POST", "/items", [1, 2])[0], 400)
            self.assertEqual(self._request(server, "POST", "/items", {})[0], 400)
            self.assertEqual(self._request(server, "POST", "/items", "x" * 65537)[0], 413)
            self.assertEqual(self._request(server, "GET", "/unknown")[0], 404)
            self.assertEqual(self._request(server, "PUT", "/items")[0], 404)
            self.assertEqual(
                self._request(server, "POST", "/reservations/1/release", {})[1]["status"],
                "released",
            )
            self.assertEqual(self._request(server, "GET", "/report")[1]["reserved_units"], 0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def _request(self, server, method, path, body=None):
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        connection.request(method, path, body=payload)
        response = connection.getresponse()
        raw = response.read()
        self.assertEqual(int(response.getheader("Content-Length")), len(raw))
        self.assertEqual(response.getheader("Content-Type"), "application/json")
        result = response.status, json.loads(raw)
        connection.close()
        return result
