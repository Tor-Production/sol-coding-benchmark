import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from reservation.store import Conflict, Store
from reservation.http_api import create_server


class ExtendedTests(unittest.TestCase):
    def setUp(self):
        self.path = os.path.abspath(".extended-test.db")
        for suffix in ("", "-wal", "-shm", "-journal"):
            try: os.unlink(self.path + suffix)
            except FileNotFoundError: pass

    def tearDown(self):
        for suffix in ("", "-wal", "-shm", "-journal"):
            try: os.unlink(self.path + suffix)
            except FileNotFoundError: pass

    def test_persistence_and_idempotency_after_release(self):
        store = Store(self.path)
        store.add_item(" x ", 5)
        original = store.reserve(" k ", " x ", 2)
        store.release(original["reservation_id"])
        self.assertEqual(Store(self.path).reserve("k", "x", 2)["status"], "released")
        self.assertEqual(Store(self.path).get_item("x")["available"], 5)

    def test_conflicting_retry_is_unchanged(self):
        store = Store(self.path)
        store.add_item("x", 5)
        store.reserve("key", "x", 2)
        with self.assertRaises(Conflict): store.reserve("key", "x", 3)
        self.assertEqual(store.report()["reserved_units"], 2)

    def test_http(self):
        server = create_server(self.path)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = "http://127.0.0.1:%d" % server.server_address[1]
        try:
            request = urllib.request.Request(base + "/items", data=b'{"sku":"a b","quantity":2}', headers={"Content-Type":"application/json"}, method="POST")
            with urllib.request.urlopen(request) as response:
                self.assertEqual(response.status, 201)
                self.assertEqual(int(response.headers["Content-Length"]), len(response.read()))
            with urllib.request.urlopen(base + "/items/a%20b") as response:
                self.assertEqual(json.load(response)["available"], 2)
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_concurrent_stores_do_not_oversell(self):
        Store(self.path).add_item("x", 5)
        def reserve(number):
            try:
                Store(self.path).reserve("key%d" % number, "x", 1)
                return True
            except Conflict:
                return False
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(reserve, range(20)))
        self.assertEqual(sum(results), 5)
        self.assertEqual(Store(self.path).report()["active_reservations"], 5)


if __name__ == "__main__": unittest.main()
