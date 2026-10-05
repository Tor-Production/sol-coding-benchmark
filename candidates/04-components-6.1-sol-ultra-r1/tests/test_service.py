"""Contract checks for persistence, concurrency, HTTP, and CLI integration."""

import http.client
import json
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

from reservation import Conflict, NotFound, Store
from reservation.http_api import MAX_BODY_BYTES, create_server


WORKSPACE = Path(__file__).resolve().parent.parent


class DatabaseTest(unittest.TestCase):
    def setUp(self):
        # Keep all test artifacts in the workspace; cleanup also detects open DBs.
        self.directory = WORKSPACE / (".test-state-" + uuid.uuid4().hex)
        self.directory.mkdir()
        self.addCleanup(shutil.rmtree, self.directory)
        self.db = self.directory / "stock.db"
        self.store = Store(self.db)


class StoreTests(DatabaseTest):
    def test_add_normalization_and_persistent_report(self):
        self.assertEqual(self.store.report(), {
            "items": [], "active_reservations": 0, "reserved_units": 0,
        })
        self.assertEqual(self.store.add_item(" zebra ", 4), {"sku": "zebra", "available": 4})
        self.assertEqual(self.store.add_item("zebra", 3), {"sku": "zebra", "available": 7})
        self.store.add_item("apple", 6)
        self.store.reserve("one", "zebra", 2)
        self.store.reserve("two", "apple", 3)
        expected = {
            "items": [{"sku": "apple", "available": 3}, {"sku": "zebra", "available": 5}],
            "active_reservations": 2,
            "reserved_units": 5,
        }
        self.assertEqual(Store(self.db).report(), expected)
        self.assertEqual(self.store.get_item(" apple "), expected["items"][0])

    def test_retry_and_release_across_reopen(self):
        self.store.add_item("sku", 5)
        first = self.store.reserve(" key ", " sku ", 3)
        self.assertEqual(first, {
            "reservation_id": first["reservation_id"], "idempotency_key": "key",
            "sku": "sku", "quantity": 3, "status": "active",
        })
        self.assertGreater(first["reservation_id"], 0)
        self.assertEqual(Store(self.db).reserve("key", "sku", 3), first)
        released = {**first, "status": "released"}
        self.assertEqual(Store(self.db).release(first["reservation_id"]), released)
        self.assertEqual(Store(self.db).release(first["reservation_id"]), released)
        self.assertEqual(Store(self.db).reserve("key", "sku", 3), first)
        self.assertEqual(self.store.report(), {
            "items": [{"sku": "sku", "available": 5}],
            "active_reservations": 0, "reserved_units": 0,
        })
        with self.assertRaises(Conflict):
            Store(self.db).reserve("key", "sku", 2)

    def test_failed_reservations_preserve_stock_and_keys(self):
        self.store.add_item("sku", 3)
        original = self.store.report()
        with self.assertRaises(NotFound):
            self.store.reserve("key", "missing", 1)
        with self.assertRaises(Conflict):
            self.store.reserve("key", "sku", 4)
        with self.assertRaises(NotFound):
            self.store.release(999)
        self.assertEqual(self.store.report(), original)
        first = self.store.reserve("key", "sku", 2)
        reserved = self.store.report()
        for sku, quantity in [("missing", 2), ("sku", 1)]:
            with self.subTest(sku=sku, quantity=quantity), self.assertRaises(Conflict):
                self.store.reserve("key", sku, quantity)
        self.assertEqual(self.store.report(), reserved)
        self.assertEqual(self.store.reserve("key", "sku", 2), first)

    def test_validation_has_no_side_effects(self):
        self.store.add_item("sku", 4)
        original = self.store.report()
        for value in [None, "", " \t\n", 42, True, [], {}]:
            actions = [
                lambda: self.store.add_item(value, 1),
                lambda: self.store.get_item(value),
                lambda: self.store.reserve(value, "sku", 1),
                lambda: self.store.reserve("key", value, 1),
            ]
            for index, action in enumerate(actions):
                with self.subTest(value=value, action=index), self.assertRaises(ValueError):
                    action()
        for value in [None, 0, -1, True, False, 1.0, "1", [], {}]:
            actions = [
                lambda: self.store.add_item("sku", value),
                lambda: self.store.reserve("key", "sku", value),
                lambda: self.store.release(value),
            ]
            for index, action in enumerate(actions):
                with self.subTest(value=value, action=index), self.assertRaises(ValueError):
                    action()
        self.assertEqual(self.store.report(), original)

    def test_large_python_integers_and_totals(self):
        units = 1 << 80
        self.store.add_item("sku", units)
        self.store.add_item("sku", units)
        first = self.store.reserve("one", "sku", units)
        self.store.reserve("two", "sku", units)
        self.assertEqual(self.store.report()["reserved_units"], 2 * units)
        self.store.release(first["reservation_id"])
        self.assertEqual(Store(self.db).get_item("sku")["available"], units)
        with self.assertRaises(NotFound):
            self.store.release(units)

    def test_sql_failure_rolls_back_both_updates(self):
        self.store.add_item("sku", 5)
        connection = sqlite3.connect(self.db)
        try:
            connection.execute("""CREATE TRIGGER reject_reservation BEFORE INSERT ON reservations
                BEGIN SELECT RAISE(ABORT, 'injected failure'); END""")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.reserve("key", "sku", 2)
        self.assertEqual(self.store.report(), {
            "items": [{"sku": "sku", "available": 5}],
            "active_reservations": 0, "reserved_units": 0,
        })

    def test_concurrent_add_reserve_retry_and_release(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: Store(self.db).add_item("sku", 1), range(24)))
        self.assertEqual(self.store.get_item("sku")["available"], 24)

        def reserve(index):
            try:
                return Store(self.db).reserve(f"key-{index}", "sku", 3)
            except Conflict:
                return None

        with ThreadPoolExecutor(max_workers=8) as pool:
            attempts = list(pool.map(reserve, range(16)))
        successes = [record for record in attempts if record is not None]
        self.assertEqual(len(successes), 8)
        self.assertEqual(len({r["reservation_id"] for r in successes}), 8)
        self.assertEqual(self.store.report(), {
            "items": [{"sku": "sku", "available": 0}],
            "active_reservations": 8, "reserved_units": 24,
        })
        first = successes[0]
        with ThreadPoolExecutor(max_workers=8) as pool:
            replays = list(pool.map(
                lambda _: Store(self.db).reserve(first["idempotency_key"], "sku", 3), range(16)
            ))
            releases = list(pool.map(
                lambda _: Store(self.db).release(first["reservation_id"]), range(16)
            ))
        self.assertEqual(replays, [first] * 16)
        self.assertEqual(releases, [{**first, "status": "released"}] * 16)
        self.assertEqual(self.store.get_item("sku")["available"], 3)
        self.assertEqual(self.store.report()["active_reservations"], 7)

    def test_concurrent_creation_with_the_same_key(self):
        self.store.add_item("sku", 10)
        barrier = threading.Barrier(8)

        def reserve(_):
            store = Store(self.db)
            barrier.wait(timeout=5)
            return store.reserve("same-key", "sku", 2)

        with ThreadPoolExecutor(max_workers=8) as pool:
            records = list(pool.map(reserve, range(8)))
        self.assertEqual(records, [records[0]] * 8)
        self.assertEqual(self.store.report(), {
            "items": [{"sku": "sku", "available": 8}],
            "active_reservations": 1, "reserved_units": 2,
        })

    def test_reports_are_consistent_during_writes(self):
        self.store.add_item("sku", 5)
        started = threading.Event()

        def mutate():
            writer = Store(self.db)
            started.set()
            for index in range(30):
                reservation = writer.reserve(f"key-{index}", "sku", 1)
                writer.release(reservation["reservation_id"])

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(mutate)
            self.assertTrue(started.wait(5))
            for _ in range(50):
                report = self.store.report()
                self.assertEqual(report["items"][0]["available"] + report["reserved_units"], 5)
                self.assertEqual(report["active_reservations"], report["reserved_units"])
            future.result(timeout=10)


class HTTPTests(DatabaseTest):
    def setUp(self):
        super().setUp()
        self.server = create_server(self.db)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.assertFalse(self.thread.is_alive())

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        try:
            if isinstance(body, dict):
                body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            payload = response.read()
            self.assertEqual(response.getheader("Content-Type"), "application/json")
            self.assertEqual(int(response.getheader("Content-Length")), len(payload))
            return response.status, json.loads(payload.decode("utf-8"))
        finally:
            connection.close()

    def assert_error(self, expected, method, path, body=None, headers=None):
        status, record = self.request(method, path, body, headers)
        self.assertEqual(status, expected)
        self.assertEqual(set(record), {"error"})
        self.assertIsInstance(record["error"], str)

    def test_routes_unicode_and_shared_state(self):
        self.assertEqual(self.request("GET", "/health"), (200, {"ok": True}))
        sku = "café / 雪 + ? # %"
        self.assertEqual(self.request("POST", "/items", {"sku": f" {sku} ", "quantity": 5}),
                         (201, {"sku": sku, "available": 5}))
        self.assertEqual(self.request("GET", "/items/" + quote(sku, safe="")),
                         (200, {"sku": sku, "available": 5}))
        body = {"idempotency_key": " key ", "sku": sku, "quantity": 2}
        status, first = self.request("POST", "/reservations", body)
        self.assertEqual(status, 201)
        self.assertEqual(self.request("POST", "/reservations", body), (201, first))
        self.assertEqual(Store(self.db).get_item(sku)["available"], 3)
        path = f"/reservations/{first['reservation_id']}/release"
        released = {**first, "status": "released"}
        self.assertEqual(self.request("POST", path, {}), (200, released))
        self.assertEqual(self.request("POST", path, {}), (200, released))
        self.assertEqual(self.request("POST", "/reservations", body), (201, first))
        self.assertEqual(self.request("GET", "/report"), (200, self.store.report()))

    def test_domain_errors_and_unknown_routes(self):
        self.store.add_item("sku", 2)
        self.store.reserve("key", "sku", 1)
        self.assert_error(404, "GET", "/items/missing")
        self.assert_error(404, "POST", "/reservations/999/release", {})
        self.assert_error(404, "POST", f"/reservations/{1 << 80}/release", {})
        self.assert_error(404, "POST", "/reservations", {
            "idempotency_key": "new", "sku": "missing", "quantity": 1,
        })
        for body in [
            {"idempotency_key": "new", "sku": "sku", "quantity": 2},
            {"idempotency_key": "key", "sku": "sku", "quantity": 2},
            {"idempotency_key": "key", "sku": "missing", "quantity": 1},
        ]:
            self.assert_error(409, "POST", "/reservations", body)
        for method, path in [("GET", "/missing"), ("POST", "/missing"),
                             ("GET", "/items/sku/extra"), ("DELETE", "/items/sku")]:
            self.assert_error(404, method, path, {})

    def test_bad_json_fields_and_values_do_not_stop_server(self):
        for body in [b"", b"{", b"[]", b"null", b"1", b"true", b'"text"', b"\xff",
                     b'{"sku":"sku","quantity":NaN}', b"[" * 2000 + b"]" * 2000]:
            with self.subTest(body=body[:50]):
                self.assert_error(400, "POST", "/items", body)
        for body in [{}, {"sku": "sku"}, {"quantity": 1},
                     {"sku": " ", "quantity": 1}, {"sku": 2, "quantity": 1},
                     {"sku": "sku", "quantity": True}, {"sku": "sku", "quantity": 0},
                     {"sku": "sku", "quantity": 1.5}, {"sku": "sku", "quantity": "1"}]:
            with self.subTest(body=body):
                self.assert_error(400, "POST", "/items", body)
        self.assert_error(400, "POST", "/reservations", {"sku": "sku", "quantity": 1})
        self.assert_error(400, "POST", "/reservations", {
            "idempotency_key": " ", "sku": "sku", "quantity": 1,
        })
        for raw_id in ["0", "-1", "1.5", "True", "abc"]:
            self.assert_error(400, "POST", f"/reservations/{raw_id}/release", {})
        self.assert_error(400, "GET", "/items/%20%20")
        self.assertEqual(self.request("GET", "/health"), (200, {"ok": True}))
        self.assertEqual(self.store.report()["items"], [])

    def test_body_limit_and_content_length_validation(self):
        self.assert_error(413, "POST", "/items", b"", {"Content-Length": str(MAX_BODY_BYTES + 1)})
        self.assert_error(413, "POST", "/items", b"", {"Content-Length": "9" * 100})
        for length in ["-1", "abc", "+2"]:
            self.assert_error(400, "POST", "/items", b"{}", {"Content-Length": length})
        body = b'{"sku":"sku","quantity":1}'
        body += b" " * (MAX_BODY_BYTES - len(body))
        self.assertEqual(self.request("POST", "/items", body), (201, {"sku": "sku", "available": 1}))
        self.assertEqual(self.request("GET", "/health"), (200, {"ok": True}))

    def test_concurrent_http_stock_and_idempotency(self):
        self.store.add_item("sku", 6)

        def reserve(index):
            return self.request("POST", "/reservations", {
                "idempotency_key": f"key-{index}", "sku": "sku", "quantity": 2,
            })

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(reserve, range(10)))
        successes = [record for status, record in results if status == 201]
        self.assertEqual(len(successes), 3)
        self.assertEqual(sum(status == 409 for status, _ in results), 7)
        self.assertEqual(self.store.get_item("sku")["available"], 0)
        first = successes[0]
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.request("POST", "/reservations", {
                "idempotency_key": first["idempotency_key"], "sku": "sku", "quantity": 2,
            }), range(10)))
        self.assertEqual(results, [(201, first)] * 10)
        self.assertEqual(self.store.report()["reserved_units"], 6)


class CLITests(DatabaseTest):
    def cli(self, *arguments):
        return subprocess.run(
            [sys.executable, "-m", "reservation", "--db", str(self.db), *arguments],
            cwd=WORKSPACE, capture_output=True, text=True, encoding="utf-8", timeout=10,
        )

    def successful(self, *arguments):
        result = self.cli(*arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(result.stdout.splitlines()), 1)
        return json.loads(result.stdout)

    def test_cli_persistence_and_store_interoperation(self):
        self.assertEqual(self.successful("add", "--sku", " sku ", "--quantity", "5"),
                         {"sku": "sku", "available": 5})
        first = self.successful("reserve", "--key", " key ", "--sku", "sku", "--quantity", "2")
        self.assertEqual(self.successful("reserve", "--key", "key", "--sku", "sku", "--quantity", "2"), first)
        self.assertEqual(Store(self.db).get_item("sku")["available"], 3)
        self.assertEqual(self.successful("release", "--id", str(first["reservation_id"])),
                         {**first, "status": "released"})
        self.store.add_item("sku", 1)
        self.assertEqual(self.successful("report"), self.store.report())
        self.assertEqual(self.successful("reserve", "--key", "key", "--sku", "sku", "--quantity", "2"), first)

    def test_cli_failures_are_json_on_stderr(self):
        self.store.add_item("sku", 1)
        cases = [
            ("reserve", "--key", "key", "--sku", "sku", "--quantity", "2"),
            ("reserve", "--key", "key", "--sku", "missing", "--quantity", "1"),
            ("release", "--id", "999"),
            ("add", "--sku", " ", "--quantity", "1"),
            ("add", "--sku", "sku", "--quantity", "0"),
            ("add", "--sku", "sku", "--quantity", "no"),
            ("add", "--sku", "sku"),
            ("release", "--id", "-1"),
            ("serve", "--port", "65536"),
            ("unknown",),
            (),
        ]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result = self.cli(*arguments)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                record = json.loads(result.stderr)
                self.assertEqual(set(record), {"error"})
                self.assertIsInstance(record["error"], str)
        self.assertEqual(self.store.get_item("sku")["available"], 1)

    def test_cli_serve_blocks_and_shares_database(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        process = subprocess.Popen(
            [sys.executable, "-m", "reservation", "--db", str(self.db),
             "serve", "--host", "127.0.0.1", "--port", str(port)],
            cwd=WORKSPACE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

        def request(method, path, body=None):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
            try:
                connection.request(method, path, body=json.dumps(body) if body is not None else None)
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()

        try:
            deadline = time.monotonic() + 5
            while True:
                try:
                    health = request("GET", "/health")
                    break
                except OSError:
                    if process.poll() is not None or time.monotonic() >= deadline:
                        self.fail("CLI server did not start")
                    time.sleep(0.02)
            self.assertEqual(health, (200, {"ok": True}))
            self.assertIsNone(process.poll())
            self.assertEqual(request("POST", "/items", {"sku": "sku", "quantity": 4}),
                             (201, {"sku": "sku", "available": 4}))
            self.assertEqual(self.successful("report"), self.store.report())
            self.successful("reserve", "--key", "key", "--sku", "sku", "--quantity", "2")
            self.assertEqual(request("GET", "/report"), (200, self.store.report()))
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=5)
            self.assertEqual(stdout, b"")
            self.assertEqual(stderr, b"")


if __name__ == "__main__":
    unittest.main()
