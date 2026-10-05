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
from reservation.store import Conflict, Store


class ContractExtraTests(unittest.TestCase):
    def setUp(self):
        self.db = Path.cwd() / ("test_" + uuid.uuid4().hex + ".db")
        self.addCleanup(lambda: self.db.unlink(missing_ok=True))
        self.store = Store(str(self.db))

    def test_persistence_retry_release_and_validation(self):
        self.assertEqual(self.store.add_item(" b ", 4), {"sku": "b", "available": 4})
        self.assertEqual(self.store.add_item("a", 2)["available"], 2)
        self.assertEqual(self.store.add_item("b", 1)["available"], 5)
        first = self.store.reserve(" key ", "b", 3)
        self.assertEqual(Store(str(self.db)).reserve("key", " b ", 3), first)
        with self.assertRaises(Conflict):
            self.store.reserve("key", "b", 2)
        self.assertEqual(self.store.release(first["reservation_id"])["status"], "released")
        self.assertEqual(self.store.release(first["reservation_id"])["status"], "released")
        self.assertEqual(Store(str(self.db)).reserve("key", "b", 3)["status"], "released")
        self.assertEqual(self.store.report(), {
            "items": [{"sku": "a", "available": 2}, {"sku": "b", "available": 5}],
            "active_reservations": 0, "reserved_units": 0})
        for invalid in (True, 0, -1, 1.5, "1"):
            with self.assertRaises(ValueError):
                self.store.add_item("b", invalid)

    def test_concurrent_reservations(self):
        self.store.add_item("sku", 10)

        def attempt(number):
            try:
                return Store(str(self.db)).reserve(str(number), "sku", 1)
            except Conflict:
                return None

        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(attempt, range(30)))
        self.assertEqual(sum(result is not None for result in results), 10)
        self.assertEqual(self.store.report()["reserved_units"], 10)
        self.assertEqual(self.store.get_item("sku")["available"], 0)

    def test_http_and_cli_share_db(self):
        server = create_server(str(self.db))
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        self.addCleanup(worker.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def request(method, path, body=None, headers=None):
            connection = http.client.HTTPConnection(*server.server_address)
            try:
                connection.request(method, path, body=body, headers=headers or {})
                response = connection.getresponse()
                raw = response.read()
                self.assertEqual(int(response.getheader("Content-Length")), len(raw))
                self.assertEqual(response.getheader("Content-Type"), "application/json")
                return response.status, json.loads(raw)
            finally:
                connection.close()

        self.assertEqual(request("GET", "/health"), (200, {"ok": True}))
        payload = json.dumps({"sku": "a b", "quantity": 2})
        self.assertEqual(request("POST", "/items", payload)[0], 201)
        self.assertEqual(request("GET", "/items/a%20b")[1]["available"], 2)
        reservation = json.dumps({"idempotency_key": "k", "sku": "a b", "quantity": 1})
        status, record = request("POST", "/reservations", reservation)
        self.assertEqual(status, 201)
        self.assertEqual(request("POST", "/reservations", reservation), (201, record))
        self.assertEqual(request("POST", "/reservations", "[]")[0], 400)
        self.assertEqual(request("POST", "/items", "x" * 65537)[0], 413)
        self.assertEqual(request("POST", f"/reservations/{record['reservation_id']}/release", "{}")[0], 200)
        cli = subprocess.run([sys.executable, "-m", "reservation", "--db", str(self.db), "report"],
                             capture_output=True, text=True)
        self.assertEqual(cli.returncode, 0)
        self.assertEqual(json.loads(cli.stdout)["items"][0]["available"], 2)
