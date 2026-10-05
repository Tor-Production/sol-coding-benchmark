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
from reservation.http_api import create_server


WORKSPACE = Path(__file__).resolve().parents[1]


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=WORKSPACE)
        self.addCleanup(temporary.cleanup)
        self.db_path = Path(temporary.name) / "inventory.db"
        self.store = Store(self.db_path)


class StoreTests(DatabaseTests):
    def test_persistence_idempotency_and_sorted_report(self):
        self.assertEqual(self.store.report(), {
            "items": [], "active_reservations": 0, "reserved_units": 0,
        })
        self.assertEqual(self.store.add_item(" z ", 3), {"sku": "z", "available": 3})
        self.store.add_item("a", 5)
        self.assertEqual(self.store.add_item(" a ", 2), {"sku": "a", "available": 7})
        first = self.store.reserve(" key ", " a ", 2)
        self.assertEqual(first, {
            "reservation_id": first["reservation_id"],
            "idempotency_key": "key", "sku": "a", "quantity": 2, "status": "active",
        })
        self.assertIs(type(first["reservation_id"]), int)
        self.assertGreater(first["reservation_id"], 0)
        reopened = Store(self.db_path)
        self.assertEqual(reopened.reserve("key", "a", 2), first)
        self.assertEqual(reopened.report(), {
            "items": [{"sku": "a", "available": 5}, {"sku": "z", "available": 3}],
            "active_reservations": 1, "reserved_units": 2,
        })
        released = reopened.release(first["reservation_id"])
        self.assertEqual(released, dict(first, status="released"))
        self.assertEqual(Store(self.db_path).reserve("key", "a", 2), released)
        self.assertEqual(self.store.release(first["reservation_id"]), released)
        self.assertEqual(self.store.get_item("a")["available"], 7)
        self.assertEqual(self.store.report()["active_reservations"], 0)

    def test_failures_leave_stock_and_reservations_unchanged(self):
        self.store.add_item("sku", 5)
        self.store.reserve("taken", "sku", 2)
        before = self.store.report()
        for key, sku, quantity, exception in [
            ("new", "sku", 4, Conflict),
            ("new", "missing", 1, NotFound),
            ("taken", "sku", 1, Conflict),
            ("taken", "missing", 2, Conflict),
        ]:
            with self.subTest(key=key, sku=sku, quantity=quantity):
                with self.assertRaises(exception):
                    self.store.reserve(key, sku, quantity)
                self.assertEqual(self.store.report(), before)
        with self.assertRaises(NotFound):
            self.store.release(999)
        with self.assertRaises(NotFound):
            self.store.get_item("missing")
        # A failed attempt does not consume its idempotency key.
        self.store.reserve("new", "sku", 3)
        self.assertEqual(self.store.get_item("sku")["available"], 0)

    def test_transaction_rolls_back_stock_if_insertion_fails(self):
        self.store.add_item("sku", 5)
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute("""
                CREATE TRIGGER fail_reservation BEFORE INSERT ON reservations
                BEGIN SELECT RAISE(ABORT, 'forced failure'); END
            """)
            connection.commit()
        finally:
            connection.close()
        before = self.store.report()
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.reserve("key", "sku", 2)
        self.assertEqual(self.store.report(), before)

    def test_validation(self):
        for invalid in [None, True, 1, [], {}, "", " \t\n"]:
            with self.subTest(text=invalid):
                for operation in [
                    lambda: self.store.add_item(invalid, 1),
                    lambda: self.store.get_item(invalid),
                    lambda: self.store.reserve(invalid, "sku", 1),
                    lambda: self.store.reserve("key", invalid, 1),
                ]:
                    with self.assertRaises(ValueError):
                        operation()
        for invalid in [None, True, False, 0, -1, 1.0, "1", [], {}]:
            with self.subTest(integer=invalid):
                for operation in [
                    lambda: self.store.add_item("sku", invalid),
                    lambda: self.store.reserve("key", "sku", invalid),
                    lambda: self.store.release(invalid),
                ]:
                    with self.assertRaises(ValueError):
                        operation()
        self.assertEqual(self.store.report()["items"], [])

    def test_large_positive_integers_remain_exact(self):
        units = 2**100
        self.store.add_item("sku", units)
        self.store.add_item("sku", units)
        reservation = self.store.reserve("large", "sku", units + 1)
        self.assertEqual(self.store.get_item("sku")["available"], units - 1)
        self.assertEqual(self.store.report()["reserved_units"], units + 1)
        self.store.release(reservation["reservation_id"])
        self.assertEqual(self.store.get_item("sku")["available"], units * 2)
        with self.assertRaises(NotFound):
            self.store.release(units)

    def test_separate_instances_concurrent_add_reserve_retry_and_release(self):
        def add(index):
            return Store(self.db_path).add_item("sku", 1)

        with ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(add, range(20)))
        self.assertEqual(self.store.get_item("sku")["available"], 20)

        def reserve(index):
            try:
                return Store(self.db_path).reserve(f"key-{index}", "sku", 1)
            except Conflict:
                return None

        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(reserve, range(40)))
        successes = [record for record in results if record is not None]
        self.assertEqual(len(successes), 20)
        self.assertEqual(len({record["reservation_id"] for record in successes}), 20)
        self.assertEqual(self.store.report(), {
            "items": [{"sku": "sku", "available": 0}],
            "active_reservations": 20, "reserved_units": 20,
        })

        reservation = successes[0]
        with ThreadPoolExecutor(max_workers=12) as pool:
            retries = list(pool.map(
                lambda _: Store(self.db_path).reserve(reservation["idempotency_key"], "sku", 1),
                range(20),
            ))
            releases = list(pool.map(
                lambda _: Store(self.db_path).release(reservation["reservation_id"]),
                range(20),
            ))
        self.assertEqual(retries, [reservation] * 20)
        self.assertTrue(all(record["status"] == "released" for record in releases))
        self.assertEqual(self.store.get_item("sku")["available"], 1)
        self.assertEqual(self.store.report()["reserved_units"], 19)

    def test_concurrent_first_use_of_same_key_creates_one_record(self):
        self.store.add_item("sku", 20)
        barrier = threading.Barrier(12)

        def reserve(_):
            store = Store(self.db_path)
            barrier.wait(timeout=10)
            return store.reserve("same", "sku", 2)

        with ThreadPoolExecutor(max_workers=12) as pool:
            records = list(pool.map(reserve, range(12)))
        self.assertEqual(records, [records[0]] * 12)
        self.assertEqual(self.store.report(), {
            "items": [{"sku": "sku", "available": 18}],
            "active_reservations": 1, "reserved_units": 2,
        })


class HTTPTests(DatabaseTests):
    def setUp(self):
        super().setUp()
        self.server = create_server(self.db_path)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, method, path, payload=None, *, raw=None, headers=None):
        if raw is None and payload is not None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=15)
        try:
            connection.request(method, path, raw, headers or {})
            response = connection.getresponse()
            body = response.read()
            self.assertEqual(response.getheader("Content-Type"), "application/json")
            self.assertEqual(int(response.getheader("Content-Length")), len(body))
            return response.status, json.loads(body)
        finally:
            connection.close()

    def test_routes_unicode_retry_release_and_shared_state(self):
        self.assertEqual(self.request("GET", "/health"), (200, {"ok": True}))
        sku = "café / 漢字"
        self.assertEqual(self.request("POST", "/items", {"sku": sku, "quantity": 5}),
                         (201, {"sku": sku, "available": 5}))
        self.assertEqual(self.request("GET", "/items/" + quote(sku, safe="")),
                         (200, {"sku": sku, "available": 5}))
        payload = {"idempotency_key": "order", "sku": sku, "quantity": 2}
        status, reservation = self.request("POST", "/reservations", payload)
        self.assertEqual(status, 201)
        self.assertEqual(self.request("POST", "/reservations", payload), (201, reservation))
        self.assertEqual(self.store.get_item(sku)["available"], 3)
        release_path = f"/reservations/{reservation['reservation_id']}/release"
        released = dict(reservation, status="released")
        self.assertEqual(self.request("POST", release_path, {}), (200, released))
        self.assertEqual(self.request("POST", release_path, {}), (200, released))
        self.assertEqual(self.request("POST", "/reservations", payload), (201, released))
        self.assertEqual(self.request("GET", "/report"), (200, self.store.report()))
        self.assertEqual(self.store.get_item(sku)["available"], 5)

    def test_errors_are_json_and_server_remains_available(self):
        self.store.add_item("sku", 1)
        cases = [
            ("GET", "/unknown", None, 404),
            ("GET", "/items/missing", None, 404),
            ("PUT", "/items", b"{}", 404),
            ("POST", "/items", b"{", 400),
            ("POST", "/items", b"[]", 400),
            ("POST", "/items", b"null", 400),
            ("POST", "/items", b"{}", 400),
            ("POST", "/items", b'{"sku":" ","quantity":1}', 400),
            ("POST", "/items", b'{"sku":"sku","quantity":true}', 400),
            ("POST", "/items", b'{"sku":"sku","quantity":NaN}', 400),
            ("POST", "/items", b'\xff', 400),
            ("POST", "/reservations", b'{"idempotency_key":"k","sku":"sku","quantity":2}', 409),
            ("POST", "/reservations", b'{"idempotency_key":"k","sku":"missing","quantity":1}', 404),
            ("POST", "/reservations/1/release", b"[]", 400),
            ("POST", "/reservations/0/release", b"{}", 400),
            ("POST", "/reservations/abc/release", b"{}", 400),
            ("POST", "/reservations/1_0/release", b"{}", 400),
            ("POST", "/reservations/999/release", b"{}", 404),
        ]
        for method, path, body, expected in cases:
            with self.subTest(method=method, path=path, body=body):
                status, record = self.request(method, path, raw=body)
                self.assertEqual(status, expected)
                self.assertEqual(set(record), {"error"})
                self.assertIsInstance(record["error"], str)
        self.assertEqual(self.request("GET", "/health"), (200, {"ok": True}))
        self.assertEqual(self.store.get_item("sku")["available"], 1)

    def test_body_limit_and_bad_content_lengths(self):
        for length in ["-1", "nope", "1.5"]:
            with self.subTest(length=length):
                status, record = self.request("POST", "/items", raw=b"{}",
                                              headers={"Content-Length": length})
                self.assertEqual(status, 400)
                self.assertIn("error", record)
        for raw, headers in [
            (b"{}", {"Content-Length": "65537"}),
            (b"{}", {"Content-Length": "9" * 5000}),
            (b" " * 65537, {}),
        ]:
            status, record = self.request("POST", "/items", raw=raw, headers=headers)
            self.assertEqual(status, 413)
            self.assertIn("error", record)
        payload = b'{"sku":"limit","quantity":1}'
        status, _ = self.request("POST", "/items", raw=payload + b" " * (65536 - len(payload)))
        self.assertEqual(status, 201)
        self.assertEqual(self.request("GET", "/health"), (200, {"ok": True}))

    def test_concurrent_http_reservations_do_not_oversell(self):
        self.store.add_item("sku", 8)

        def reserve(index):
            return self.request("POST", "/reservations", {
                "idempotency_key": f"http-{index}", "sku": "sku", "quantity": 1,
            })

        with ThreadPoolExecutor(max_workers=10) as pool:
            responses = list(pool.map(reserve, range(20)))
        self.assertEqual(sum(status == 201 for status, _ in responses), 8)
        self.assertEqual(sum(status == 409 for status, _ in responses), 12)
        self.assertEqual(self.store.report(), {
            "items": [{"sku": "sku", "available": 0}],
            "active_reservations": 8, "reserved_units": 8,
        })


class CLITests(DatabaseTests):
    def command(self, *arguments):
        return subprocess.run(
            [sys.executable, "-m", "reservation", "--db", str(self.db_path), *arguments],
            capture_output=True, text=True, cwd=WORKSPACE, timeout=15,
        )

    def test_cli_persistence_and_shared_schema(self):
        added = self.command("add", "--sku", " sku ", "--quantity", "5")
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertEqual(json.loads(added.stdout), {"sku": "sku", "available": 5})
        self.assertEqual(added.stderr, "")
        reserved = self.command("reserve", "--key", "key", "--sku", "sku", "--quantity", "2")
        self.assertEqual(reserved.returncode, 0, reserved.stderr)
        reservation = json.loads(reserved.stdout)
        self.assertEqual(self.store.reserve("key", "sku", 2), reservation)
        report = self.command("report")
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertEqual(json.loads(report.stdout), self.store.report())
        released = self.command("release", "--id", str(reservation["reservation_id"]))
        self.assertEqual(released.returncode, 0, released.stderr)
        self.assertEqual(json.loads(released.stdout)["status"], "released")
        self.assertEqual(self.store.get_item("sku")["available"], 5)

    def test_cli_validation_and_domain_errors(self):
        self.store.add_item("sku", 1)
        for arguments in [
            ("add", "--sku", "sku", "--quantity", "0"),
            ("add", "--sku", "sku", "--quantity", "nope"),
            ("add", "--quantity", "1"),
            ("release", "--id", "999"),
            ("reserve", "--key", "key", "--sku", "sku", "--quantity", "2"),
            ("reserve", "--key", "key", "--sku", "missing", "--quantity", "1"),
            ("serve", "--port", "-1"),
            ("unknown",),
        ]:
            with self.subTest(arguments=arguments):
                result = self.command(*arguments)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertEqual(set(json.loads(result.stderr)), {"error"})
        self.assertEqual(self.store.get_item("sku")["available"], 1)
