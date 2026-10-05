import concurrent.futures
import http.client
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import quote

from reservation.http_api import create_server
from reservation.store import Conflict, NotFound, Store


class StoreContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / "stock.db")
        self.store = Store(self.db)

    def test_persistence_retry_release_and_report(self):
        self.assertEqual(self.store.add_item(" b ", 4), {"sku": "b", "available": 4})
        self.assertEqual(self.store.add_item("a", 3), {"sku": "a", "available": 3})
        self.assertEqual(self.store.add_item("b", 2)["available"], 6)
        original = self.store.reserve(" key ", " b ", 2)
        reopened = Store(self.db)
        self.assertEqual(reopened.reserve("key", "b", 2), original)
        with self.assertRaises(Conflict):
            reopened.reserve("key", "b", 3)
        self.assertEqual(reopened.get_item("b")["available"], 4)
        released = reopened.release(original["reservation_id"])
        self.assertEqual(reopened.release(original["reservation_id"]), released)
        self.assertEqual(reopened.reserve("key", "b", 2), released)
        self.assertEqual(
            Store(self.db).report(),
            {
                "items": [
                    {"sku": "a", "available": 3},
                    {"sku": "b", "available": 6},
                ],
                "active_reservations": 0,
                "reserved_units": 0,
            },
        )

    def test_failure_does_not_change_stock_or_reservations(self):
        self.store.add_item("sku", 1)
        for key, sku, quantity, error in [
            ("missing", "other", 1, NotFound),
            ("too-many", "sku", 2, Conflict),
        ]:
            with self.assertRaises(error):
                self.store.reserve(key, sku, quantity)
        self.assertEqual(self.store.report()["active_reservations"], 0)
        self.assertEqual(self.store.get_item("sku")["available"], 1)
        with self.assertRaises(NotFound):
            self.store.release(999)

    def test_invalid_arguments_and_large_integer(self):
        for value in (None, "", "  ", 1, True):
            with self.assertRaises(ValueError):
                self.store.add_item(value, 1)
        for value in (None, 0, -1, True, 1.5, "1"):
            with self.assertRaises(ValueError):
                self.store.add_item("sku", value)
            with self.assertRaises(ValueError):
                self.store.release(value)
        huge = 2**80
        self.assertEqual(self.store.add_item("huge", huge)["available"], huge)
        self.assertEqual(self.store.reserve("large", "huge", huge)["quantity"], huge)
        self.assertEqual(self.store.report()["reserved_units"], huge)

    def test_in_memory_store_keeps_state_across_method_connections(self):
        memory = Store(":memory:")
        memory.add_item("sku", 2)
        reservation = memory.reserve("key", "sku", 1)
        self.assertEqual(memory.get_item("sku")["available"], 1)
        self.assertEqual(memory.release(reservation["reservation_id"])["status"], "released")

    def test_separate_stores_do_not_oversell_or_duplicate_a_key(self):
        self.store.add_item("sku", 5)
        stores = [Store(self.db) for _ in range(12)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            futures = [
                pool.submit(stores[i].reserve, f"key-{i}", "sku", 1)
                for i in range(12)
            ]
            results = []
            for future in futures:
                try:
                    results.append(future.result())
                except Conflict:
                    pass
        self.assertEqual(len(results), 5)
        self.assertEqual(len({r["reservation_id"] for r in results}), 5)
        self.assertEqual(self.store.get_item("sku")["available"], 0)
        self.store.add_item("sku", 1)
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            futures = [pool.submit(s.reserve, "shared", "sku", 1) for s in stores]
            retries = [future.result() for future in futures]
        self.assertTrue(all(record == retries[0] for record in retries))
        self.assertEqual(self.store.report()["active_reservations"], 6)
        self.assertEqual(self.store.get_item("sku")["available"], 0)


class HTTPContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / "stock.db")
        self.server = create_server(self.db)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, method, path, body=None, raw=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        if raw is None and body is not None:
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"} if raw is not None else {}
        try:
            connection.request(method, path, body=raw, headers=headers)
            response = connection.getresponse()
            payload = response.read()
            self.assertEqual(response.getheader("Content-Type"), "application/json")
            self.assertEqual(int(response.getheader("Content-Length")), len(payload))
            return response.status, json.loads(payload)
        finally:
            connection.close()

    def test_routes_errors_and_utf8_length(self):
        sku = "blue / café"
        self.assertEqual(self.request("GET", "/health"), (200, {"ok": True}))
        self.assertEqual(
            self.request("POST", "/items", {"sku": f" {sku} ", "quantity": 2}),
            (201, {"sku": sku, "available": 2}),
        )
        self.assertEqual(
            self.request("GET", "/items/" + quote(sku, safe="")),
            (200, {"sku": sku, "available": 2}),
        )
        reservation = self.request(
            "POST", "/reservations", {"idempotency_key": "key", "sku": sku, "quantity": 1}
        )[1]
        self.assertEqual(
            self.request(
                "POST", "/reservations", {"idempotency_key": "key", "sku": sku, "quantity": 1}
            ),
            (201, reservation),
        )
        released = self.request(
            "POST", f"/reservations/{reservation['reservation_id']}/release", {}
        )[1]
        self.assertEqual(released["status"], "released")
        self.assertEqual(
            self.request("POST", f"/reservations/{reservation['reservation_id']}/release", {}),
            (200, released),
        )
        self.assertEqual(self.request("GET", "/report")[1]["reserved_units"], 0)

        self.assertEqual(self.request("GET", "/unknown")[0], 404)
        self.assertEqual(self.request("GET", "/items/missing")[0], 404)
        self.assertEqual(
            self.request("POST", "/reservations", {"idempotency_key": "key", "sku": sku, "quantity": 2})[0],
            409,
        )
        for body in ({"sku": "x"}, {"sku": "x", "quantity": True}):
            self.assertEqual(self.request("POST", "/items", body)[0], 400)
        self.assertEqual(self.request("POST", "/items", raw=b"{")[0], 400)
        self.assertEqual(self.request("POST", "/items", raw=b"[]")[0], 400)
        self.assertEqual(self.request("POST", "/items", raw=b'{"sku":"x","quantity":1,"bad":NaN}')[0], 400)
        self.assertEqual(self.request("POST", "/items", raw=b"x" * 65537)[0], 413)
        self.assertEqual(self.request("GET", "/health"), (200, {"ok": True}))

    def test_concurrent_requests_obey_stock_limit(self):
        self.request("POST", "/items", {"sku": "sku", "quantity": 3})

        def reserve(i):
            return self.request(
                "POST", "/reservations",
                {"idempotency_key": f"http-{i}", "sku": "sku", "quantity": 1},
            )[0]

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            statuses = list(pool.map(reserve, range(8)))
        self.assertEqual(statuses.count(201), 3)
        self.assertEqual(statuses.count(409), 5)
        self.assertEqual(self.request("GET", "/items/sku")[1]["available"], 0)


class CLIContractTests(unittest.TestCase):
    def test_cli_shares_state_and_reports_errors_as_json(self):
        with tempfile.TemporaryDirectory() as directory:
            db = str(Path(directory) / "stock.db")

            def run(*args):
                return subprocess.run(
                    [sys.executable, "-m", "reservation", "--db", db, *args],
                    capture_output=True, text=True, check=False,
                )

            added = run("add", "--sku", "widget", "--quantity", "4")
            self.assertEqual(added.returncode, 0)
            self.assertEqual(json.loads(added.stdout), {"sku": "widget", "available": 4})
            Store(db).reserve("order", "widget", 2)
            reported = run("report")
            self.assertEqual(reported.returncode, 0)
            self.assertEqual(json.loads(reported.stdout)["reserved_units"], 2)
            failed = run("add", "--sku", "widget", "--quantity", "0")
            self.assertEqual(failed.returncode, 2)
            self.assertEqual(failed.stdout, "")
            self.assertIn("error", json.loads(failed.stderr))


if __name__ == "__main__":
    unittest.main()
