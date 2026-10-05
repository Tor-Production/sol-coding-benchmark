import json
import os
import threading
import unittest
from http.client import HTTPConnection

from reservation.http_api import create_server
from reservation.store import Conflict, Store


class AdditionalTests(unittest.TestCase):
    def setUp(self):
        self.path = os.path.abspath(".test-reservation.db")
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                os.remove(self.path + suffix)
            except FileNotFoundError:
                pass

    def tearDown(self):
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                os.remove(self.path + suffix)
            except FileNotFoundError:
                pass

    def test_persistence_idempotency_and_report(self):
        store = Store(self.path)
        self.assertEqual(store.add_item(" a ", 4), {"sku": "a", "available": 4})
        first = store.reserve(" k ", " a ", 3)
        store.release(first["reservation_id"])
        reopened = Store(self.path)
        self.assertEqual(reopened.reserve("k", "a", 3), {**first, "status": "released"})
        self.assertEqual(reopened.get_item("a")["available"], 4)
        with self.assertRaises(Conflict):
            reopened.reserve("k", "a", 2)
        self.assertEqual(reopened.report()["reserved_units"], 0)

    def test_concurrent_no_oversell(self):
        Store(self.path).add_item("a", 1)
        outcomes = []
        def reserve(key):
            try:
                outcomes.append(Store(self.path).reserve(key, "a", 1)["status"])
            except Conflict:
                outcomes.append("conflict")
        threads = [threading.Thread(target=reserve, args=(str(i),)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertCountEqual(outcomes, ["active", "conflict"])

    def test_http(self):
        server = create_server(self.path)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            connection = HTTPConnection(*server.server_address)
            body = json.dumps({"sku": "a", "quantity": 2})
            connection.request("POST", "/items", body, {"Content-Type": "application/json"})
            response = connection.getresponse()
            self.assertEqual(response.status, 201)
            self.assertEqual(json.loads(response.read())["available"], 2)
            connection.request("GET", "/report")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(int(response.getheader("Content-Length")), len(response.read()))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
