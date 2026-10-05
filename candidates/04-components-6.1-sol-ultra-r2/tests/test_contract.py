"""Additional contract checks, including persistence and concurrent clients."""

import http.client
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

from reservation import Conflict, NotFound, Store
from reservation.http_api import MAX_BODY_SIZE, create_server


WORKSPACE = Path(__file__).resolve().parent.parent


class DatabaseCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=WORKSPACE)
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "stock.db"
        self.store = Store(self.db)

    def parallel(self, count, action):
        barrier = threading.Barrier(count)

        def call(index):
            store = Store(self.db)
            barrier.wait(timeout=10)
            return action(store, index)

        with ThreadPoolExecutor(max_workers=count) as executor:
            return list(executor.map(call, range(count)))


class StoreContractTests(DatabaseCase):
    def test_empty_report_addition_normalization_and_sorting(self):
        self.assertEqual(self.store.report(), {"items": [], "active_reservations": 0, "reserved_units": 0})
        self.assertEqual(self.store.add_item(" z ", 2), {"sku": "z", "available": 2})
        self.assertEqual(self.store.add_item("z", 3), {"sku": "z", "available": 5})
        self.store.add_item("a", 4)
        self.assertEqual(self.store.get_item(" z\t"), {"sku": "z", "available": 5})
        self.assertEqual(self.store.report(), {
            "items": [{"sku": "a", "available": 4}, {"sku": "z", "available": 5}],
            "active_reservations": 0,
            "reserved_units": 0,
        })

    def test_retry_release_and_reopen(self):
        self.store.add_item("sku", 5)
        original = self.store.reserve(" key ", " sku ", 2)
        self.assertEqual(original, {
            "reservation_id": original["reservation_id"], "idempotency_key": "key",
            "sku": "sku", "quantity": 2, "status": "active",
        })
        self.assertGreater(original["reservation_id"], 0)
        reopened = Store(self.db)
        self.assertEqual(reopened.reserve("key", "sku", 2), original)
        self.assertEqual(reopened.report(), {
            "items": [{"sku": "sku", "available": 3}], "active_reservations": 1, "reserved_units": 2,
        })
        released = reopened.release(original["reservation_id"])
        self.assertEqual(released, {**original, "status": "released"})
        reopened = Store(self.db)
        self.assertEqual(reopened.release(original["reservation_id"]), released)
        self.assertEqual(reopened.reserve("key", "sku", 2), released)
        self.assertEqual(reopened.report(), {
            "items": [{"sku": "sku", "available": 5}], "active_reservations": 0, "reserved_units": 0,
        })
        with self.assertRaises(Conflict):
            reopened.reserve("key", "sku", 1)

    def test_failures_do_not_change_stock_or_reservations(self):
        self.store.add_item("sku", 3)
        self.store.reserve("key", "sku", 1)
        before = self.store.report()
        for key, sku, quantity, exception in [
            ("key", "sku", 2, Conflict),
            ("key", "missing", 1, Conflict),
            ("another", "sku", 3, Conflict),
            ("another", "missing", 1, NotFound),
        ]:
            with self.subTest(key=key, sku=sku, quantity=quantity):
                with self.assertRaises(exception):
                    self.store.reserve(key, sku, quantity)
                self.assertEqual(self.store.report(), before)
        with self.assertRaises(NotFound):
            self.store.get_item("missing")
        with self.assertRaises(NotFound):
            self.store.release(999)
        self.assertEqual(self.store.report(), before)
        # A failed attempt does not claim its key.
        self.assertEqual(self.store.reserve("another", "sku", 2)["quantity"], 2)

    def test_invalid_arguments_raise_value_error_without_changes(self):
        self.store.add_item("sku", 3)
        before = self.store.report()
        for invalid in [None, True, False, 0, -1, 1.0, "1", [], {}]:
            for method, args in [
                (self.store.add_item, ("sku", invalid)),
                (self.store.reserve, ("key", "sku", invalid)),
                (self.store.release, (invalid,)),
            ]:
                with self.subTest(method=method.__name__, invalid=invalid):
                    with self.assertRaises(ValueError):
                        method(*args)
        for invalid in [None, True, 1, "", " \t\n", [], {}]:
            for method, args in [
                (self.store.get_item, (invalid,)),
                (self.store.add_item, (invalid, 1)),
                (self.store.reserve, (invalid, "sku", 1)),
                (self.store.reserve, ("key", invalid, 1)),
            ]:
                with self.subTest(method=method.__name__, invalid=invalid):
                    with self.assertRaises(ValueError):
                        method(*args)
        self.assertEqual(self.store.report(), before)

    def test_large_python_integers_remain_exact(self):
        quantity = 2 ** 100
        self.store.add_item("sku", quantity)
        self.store.add_item("sku", quantity)
        record = self.store.reserve("key", "sku", quantity + 1)
        self.assertEqual(self.store.report(), {
            "items": [{"sku": "sku", "available": quantity - 1}],
            "active_reservations": 1, "reserved_units": quantity + 1,
        })
        self.store.release(record["reservation_id"])
        self.assertEqual(Store(self.db).get_item("sku")["available"], quantity * 2)
        with self.assertRaises(NotFound):
            self.store.release(quantity)

    def test_database_failure_rolls_back_stock_subtraction(self):
        self.store.add_item("sku", 5)
        with sqlite3.connect(self.db) as connection:
            connection.execute("""
                CREATE TRIGGER reject_reservations BEFORE INSERT ON reservations
                BEGIN SELECT RAISE(ABORT, 'simulated failure'); END
            """)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.reserve("key", "sku", 2)
        self.assertEqual(self.store.report(), {
            "items": [{"sku": "sku", "available": 5}], "active_reservations": 0, "reserved_units": 0,
        })
        connection.close()

    def test_concurrent_adds_have_no_lost_updates(self):
        records = self.parallel(12, lambda store, index: store.add_item("sku", 3))
        self.assertEqual(sorted(record["available"] for record in records), list(range(3, 37, 3)))
        self.assertEqual(self.store.get_item("sku")["available"], 36)

    def test_concurrent_reservations_cannot_oversell(self):
        self.store.add_item("sku", 5)

        def reserve(store, index):
            try:
                return store.reserve(f"key-{index}", "sku", 1)
            except Conflict:
                return None

        records = [record for record in self.parallel(12, reserve) if record is not None]
        self.assertEqual(len(records), 5)
        self.assertEqual(len({record["reservation_id"] for record in records}), 5)
        self.assertEqual(self.store.report(), {
            "items": [{"sku": "sku", "available": 0}], "active_reservations": 5, "reserved_units": 5,
        })

    def test_concurrent_retries_and_releases_act_once(self):
        self.store.add_item("sku", 5)
        records = self.parallel(12, lambda store, index: store.reserve("key", "sku", 2))
        self.assertTrue(all(record == records[0] for record in records))
        self.assertEqual(self.store.report()["active_reservations"], 1)
        released = self.parallel(12, lambda store, index: store.release(records[0]["reservation_id"]))
        self.assertTrue(all(record == {**records[0], "status": "released"} for record in released))
        self.assertEqual(self.store.get_item("sku")["available"], 5)
        self.assertEqual(self.store.report()["reserved_units"], 0)


class HTTPContractTests(DatabaseCase):
    def setUp(self):
        super().setUp()
        self.server = create_server(self.db)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        self.assertFalse(self.thread.is_alive())

    def request(self, method, path, record=None, raw=None, headers=None):
        if raw is None and record is not None:
            raw = json.dumps(record, ensure_ascii=False).encode("utf-8")
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=10)
        try:
            connection.request(method, path, body=raw, headers=headers or {"Content-Type": "application/json"})
            response = connection.getresponse()
            body = response.read()
            self.assertEqual(response.getheader("Content-Type"), "application/json")
            self.assertEqual(int(response.getheader("Content-Length")), len(body))
            return response.status, json.loads(body.decode("utf-8"))
        finally:
            connection.close()

    def assert_error(self, status, response):
        self.assertEqual(response[0], status)
        self.assertEqual(set(response[1]), {"error"})
        self.assertIsInstance(response[1]["error"], str)
        self.assertTrue(response[1]["error"])

    def test_routes_unicode_and_shared_state(self):
        self.assertEqual(self.request("GET", "/health"), (200, {"ok": True}))
        sku = "café/茶 ?#%"
        self.assertEqual(self.request("POST", "/items", {"sku": " " + sku + " ", "quantity": 5}),
                         (201, {"sku": sku, "available": 5}))
        self.assertEqual(self.request("GET", "/items/" + quote(sku, safe="")),
                         (200, {"sku": sku, "available": 5}))
        request = {"idempotency_key": " key ", "sku": sku, "quantity": 2}
        status, original = self.request("POST", "/reservations", request)
        self.assertEqual(status, 201)
        self.assertEqual(self.request("POST", "/reservations", request), (201, original))
        self.assertEqual(self.store.get_item(sku)["available"], 3)
        self.assertEqual(self.request("GET", "/report"), (200, self.store.report()))
        route = f"/reservations/{original['reservation_id']}/release"
        released = {**original, "status": "released"}
        self.assertEqual(self.request("POST", route, {}), (200, released))
        self.assertEqual(self.request("POST", route, {}), (200, released))
        self.assertEqual(self.request("POST", "/reservations", request), (201, released))
        self.assertEqual(self.store.get_item(sku)["available"], 5)

    def test_malformed_and_invalid_requests_do_not_kill_server(self):
        for body in [b"", b"{", b"[]", b"null", b'"text"', b"1", b"\xff", b'{"extra": NaN}']:
            with self.subTest(body=body):
                self.assert_error(400, self.request("POST", "/items", raw=body))
        for record in [{}, {"sku": "sku"}, {"quantity": 1}, {"sku": " ", "quantity": 1},
                       {"sku": None, "quantity": 1}]:
            with self.subTest(record=record):
                self.assert_error(400, self.request("POST", "/items", record))
        for quantity in [None, True, False, 0, -1, 1.5, "1", []]:
            with self.subTest(quantity=quantity):
                self.assert_error(400, self.request("POST", "/items", {"sku": "sku", "quantity": quantity}))
        self.assert_error(400, self.request("POST", "/reservations", {"sku": "sku", "quantity": 1}))
        self.assert_error(400, self.request("POST", "/reservations/0/release", {}))
        self.assert_error(400, self.request("POST", "/reservations/text/release", {}))
        self.assert_error(400, self.request("POST", "/reservations/1/release", []))
        self.assertEqual(self.request("GET", "/health"), (200, {"ok": True}))
        self.assertEqual(self.store.report()["items"], [])

    def test_domain_errors_and_unknown_routes(self):
        self.store.add_item("sku", 2)
        self.store.reserve("key", "sku", 1)
        before = self.store.report()
        self.assert_error(409, self.request("POST", "/reservations", {
            "idempotency_key": "key", "sku": "sku", "quantity": 2,
        }))
        self.assert_error(409, self.request("POST", "/reservations", {
            "idempotency_key": "another", "sku": "sku", "quantity": 2,
        }))
        self.assert_error(404, self.request("POST", "/reservations", {
            "idempotency_key": "another", "sku": "missing", "quantity": 1,
        }))
        self.assert_error(404, self.request("POST", "/reservations/999/release", {}))
        for method, path in [("GET", "/items/missing"), ("GET", "/missing"),
                             ("GET", "/items/sku/extra"), ("POST", "/missing"), ("PUT", "/items")]:
            with self.subTest(method=method, path=path):
                self.assert_error(404, self.request(method, path, {}))
        self.assertEqual(self.store.report(), before)

    def test_body_limits_and_content_length_errors(self):
        self.assert_error(413, self.request("POST", "/items", raw=b"", headers={"Content-Length": str(MAX_BODY_SIZE + 1)}))
        self.assert_error(413, self.request("GET", "/health", raw=b"", headers={"Content-Length": "9" * 100}))
        for length in ["-1", "text", "1.5"]:
            with self.subTest(length=length):
                self.assert_error(400, self.request("POST", "/items", raw=b"", headers={"Content-Length": length}))
        raw = b'{"sku": "sku", "quantity": 1}'
        raw += b" " * (MAX_BODY_SIZE - len(raw))
        self.assertEqual(self.request("POST", "/items", raw=raw), (201, {"sku": "sku", "available": 1}))
        self.assertEqual(self.request("GET", "/health"), (200, {"ok": True}))

    def test_concurrent_http_requests_cannot_oversell(self):
        self.store.add_item("sku", 4)

        def reserve(index):
            return self.request("POST", "/reservations", {
                "idempotency_key": f"key-{index}", "sku": "sku", "quantity": 1,
            })

        with ThreadPoolExecutor(max_workers=8) as executor:
            responses = list(executor.map(reserve, range(12)))
        self.assertEqual(sum(status == 201 for status, record in responses), 4)
        self.assertEqual(sum(status == 409 for status, record in responses), 8)
        self.assertEqual(self.store.report(), {
            "items": [{"sku": "sku", "available": 0}], "active_reservations": 4, "reserved_units": 4,
        })

    def test_concurrent_http_retries_and_releases_act_once(self):
        self.store.add_item("sku", 1)

        def reserve(index):
            return self.request("POST", "/reservations", {"idempotency_key": "key", "sku": "sku", "quantity": 1})

        with ThreadPoolExecutor(max_workers=8) as executor:
            responses = list(executor.map(reserve, range(12)))
            self.assertTrue(all(response == responses[0] for response in responses))
            self.assertEqual(responses[0][0], 201)
            original = responses[0][1]
            route = f"/reservations/{original['reservation_id']}/release"
            releases = list(executor.map(lambda index: self.request("POST", route, {}), range(12)))
        self.assertTrue(all(response == (200, {**original, "status": "released"}) for response in releases))
        self.assertEqual(self.store.get_item("sku")["available"], 1)
        self.assertEqual(self.store.report()["active_reservations"], 0)


class CLIContractTests(DatabaseCase):
    def cli(self, *arguments, status=0):
        result = subprocess.run(
            [sys.executable, "-m", "reservation", "--db", str(self.db), *arguments],
            cwd=WORKSPACE, capture_output=True, text=True, encoding="utf-8", timeout=10,
        )
        self.assertEqual(result.returncode, status, result.stderr)
        output = result.stdout if status == 0 else result.stderr
        self.assertEqual(result.stderr if status == 0 else result.stdout, "")
        self.assertEqual(len(output.splitlines()), 1)
        record = json.loads(output)
        if status != 0:
            self.assertEqual(set(record), {"error"})
            self.assertIsInstance(record["error"], str)
        return record

    def test_finite_commands_and_shared_persistence(self):
        self.assertEqual(self.cli("add", "--sku", " sku ", "--quantity", "5"), {"sku": "sku", "available": 5})
        original = self.cli("reserve", "--key", "key", "--sku", "sku", "--quantity", "2")
        self.assertEqual(self.cli("reserve", "--key", "key", "--sku", "sku", "--quantity", "2"), original)
        self.assertEqual(self.store.get_item("sku")["available"], 3)
        self.assertEqual(self.cli("report"), self.store.report())
        self.cli("reserve", "--key", "key", "--sku", "sku", "--quantity", "1", status=2)
        self.cli("reserve", "--key", "another", "--sku", "sku", "--quantity", "4", status=2)
        released = {**original, "status": "released"}
        self.assertEqual(self.cli("release", "--id", str(original["reservation_id"])), released)
        self.assertEqual(self.cli("release", "--id", str(original["reservation_id"])), released)
        self.assertEqual(self.cli("reserve", "--key", "key", "--sku", "sku", "--quantity", "2"), released)
        self.assertEqual(Store(self.db).get_item("sku")["available"], 5)

    def test_validation_and_parser_errors_are_json_on_stderr(self):
        for arguments in [(), ("add",), ("unknown",), ("report", "--bogus"),
                          ("add", "--sku", "sku", "--quantity", "text"),
                          ("add", "--sku", "sku", "--quantity", "0"),
                          ("add", "--sku", " ", "--quantity", "1"),
                          ("release", "--id", "-1"), ("release", "--id", "999"),
                          ("reserve", "--key", "key", "--sku", "missing", "--quantity", "1"),
                          ("serve", "--port", "65536")]:
            with self.subTest(arguments=arguments):
                self.cli(*arguments, status=2)
        self.assertEqual(self.store.report()["items"], [])


if __name__ == "__main__":
    unittest.main()
