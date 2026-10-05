import http.client
import json
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

from reservation.http_api import create_server
from reservation.store import Conflict, Store


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.db = Path.cwd() / f"test-{uuid.uuid4().hex}.sqlite"
        self.addCleanup(lambda: self.db.unlink(missing_ok=True))
        self.store = Store(self.db)

    def test_persistence_idempotency_and_report(self):
        self.store.add_item(" b ", 4)
        self.store.add_item("a", 2)
        original = self.store.reserve(" key ", " b ", 3)
        self.assertEqual(Store(self.db).reserve("key", "b", 3), original)
        with self.assertRaises(Conflict):
            self.store.reserve("key", "b", 2)
        self.assertEqual(self.store.release(original["reservation_id"])["status"], "released")
        self.store.release(original["reservation_id"])
        self.assertEqual(Store(self.db).reserve("key", "b", 3)["status"], "released")
        self.assertEqual(Store(self.db).report(), {
            "items": [{"sku": "a", "available": 2}, {"sku": "b", "available": 4}],
            "active_reservations": 0, "reserved_units": 0,
        })

    def test_concurrent_reservations(self):
        self.store.add_item("sku", 10)

        def reserve(i):
            try:
                return Store(self.db).reserve(f"key-{i}", "sku", 1)
            except Conflict:
                return None

        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(reserve, range(30)))
        self.assertEqual(sum(result is not None for result in results), 10)
        self.assertEqual(self.store.get_item("sku")["available"], 0)
        self.assertEqual(self.store.report()["active_reservations"], 10)

    def test_http_routes_and_errors(self):
        server = create_server(self.db)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def request(method, path, body=None, headers=None):
            connection = http.client.HTTPConnection(*server.server_address)
            try:
                raw = None if body is None else json.dumps(body).encode("utf-8")
                connection.request(method, path, body=raw, headers=headers or {})
                response = connection.getresponse()
                data = response.read()
                self.assertEqual(int(response.getheader("Content-Length")), len(data))
                self.assertEqual(response.getheader("Content-Type"), "application/json")
                return response.status, json.loads(data)
            finally:
                connection.close()

        self.assertEqual(request("GET", "/health"), (200, {"ok": True}))
        self.assertEqual(request("POST", "/items", {"sku": "a/b", "quantity": 2}),
                         (201, {"sku": "a/b", "available": 2}))
        self.assertEqual(request("GET", "/items/" + quote("a/b", safe=""))[0], 200)
        status, reservation = request("POST", "/reservations", {
            "idempotency_key": "key", "sku": "a/b", "quantity": 2,
        })
        self.assertEqual(status, 201)
        self.assertEqual(request("POST", "/reservations", {
            "idempotency_key": "key", "sku": "a/b", "quantity": 2,
        }), (201, reservation))
        self.assertEqual(request("POST", f"/reservations/{reservation['reservation_id']}/release", {})[0], 200)
        self.assertEqual(request("GET", "/report")[1]["reserved_units"], 0)
        self.assertEqual(request("POST", "/items", {"sku": "x", "quantity": True})[0], 400)
        self.assertEqual(request("GET", "/missing")[0], 404)
        self.assertEqual(request("POST", "/items", {}, {"Content-Length": "65537"})[0], 413)


if __name__ == "__main__":
    unittest.main()
