import http.client
import json
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

from reservation.http_api import create_server
from reservation.store import Conflict, NotFound, Store


def parallel(count, operation):
    barrier = threading.Barrier(count)

    def invoke(index):
        barrier.wait(timeout=10)
        return operation(index)

    with ThreadPoolExecutor(max_workers=count) as executor:
        return list(executor.map(invoke, range(count)))


class DatabaseTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "inventory.db"
        self.store = Store(self.db)

    def start_server(self):
        server = create_server(self.db)
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        thread.start()

        def stop():
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.addCleanup(stop)
        return server


class StoreContractTests(DatabaseTestCase):
    def test_record_shapes_normalization_and_persistence(self):
        self.assertEqual(self.store.report(), {
            "items": [], "active_reservations": 0, "reserved_units": 0
        })
        self.assertEqual(self.store.add_item(" z ", 4), {"sku": "z", "available": 4})
        self.store.add_item("a", 7)
        self.assertEqual(self.store.add_item("z", 3), {"sku": "z", "available": 7})
        reservation = self.store.reserve(" key \t", " z ", 3)
        self.assertIs(type(reservation["reservation_id"]), int)
        self.assertGreater(reservation["reservation_id"], 0)
        self.assertEqual(reservation, {
            "reservation_id": reservation["reservation_id"],
            "idempotency_key": "key", "sku": "z", "quantity": 3, "status": "active"
        })
        reopened = Store(self.db)
        self.assertEqual(reopened.get_item(" z "), {"sku": "z", "available": 4})
        self.assertEqual(reopened.reserve("key", "z", 3), reservation)
        self.assertEqual(reopened.report(), {
            "items": [{"sku": "a", "available": 7}, {"sku": "z", "available": 4}],
            "active_reservations": 1, "reserved_units": 3
        })
        released = reopened.release(reservation["reservation_id"])
        self.assertEqual(released, dict(reservation, status="released"))
        self.assertEqual(Store(self.db).release(reservation["reservation_id"]), released)
        self.assertEqual(Store(self.db).reserve("key", "z", 3), released)
        self.assertEqual(self.store.get_item("z"), {"sku": "z", "available": 7})
        self.assertEqual(self.store.report()["active_reservations"], 0)
        self.assertEqual(self.store.report()["reserved_units"], 0)

    def test_failed_reservations_leave_state_and_key_unchanged(self):
        self.store.add_item("sku", 2)
        before = self.store.report()
        for key, sku, quantity, exception in [
            ("missing", "absent", 1, NotFound),
            ("too-many", "sku", 3, Conflict),
        ]:
            with self.subTest(key=key), self.assertRaises(exception):
                self.store.reserve(key, sku, quantity)
            self.assertEqual(self.store.report(), before)
        reservation = self.store.reserve("too-many", "sku", 2)
        self.assertEqual(self.store.reserve("too-many", "sku", 2), reservation)
        for sku, quantity in [("sku", 1), ("absent", 2)]:
            with self.subTest(sku=sku, quantity=quantity), self.assertRaises(Conflict):
                self.store.reserve("too-many", sku, quantity)
        self.assertEqual(self.store.report()["reserved_units"], 2)
        with self.assertRaises(NotFound):
            self.store.get_item("absent")
        with self.assertRaises(NotFound):
            self.store.release(10 ** 30)

    def test_validation_excludes_booleans_and_does_not_mutate_state(self):
        self.store.add_item("sku", 5)
        before = self.store.report()
        for value in [None, "", " \t\n", 1, True, [], {}]:
            with self.subTest(string=value):
                for operation in [
                    lambda: self.store.add_item(value, 1),
                    lambda: self.store.get_item(value),
                    lambda: self.store.reserve(value, "sku", 1),
                    lambda: self.store.reserve("key", value, 1),
                ]:
                    with self.assertRaises(ValueError):
                        operation()
        for value in [0, -1, True, False, 1.5, "1", None, []]:
            with self.subTest(integer=value):
                for operation in [
                    lambda: self.store.add_item("sku", value),
                    lambda: self.store.reserve("key", "sku", value),
                    lambda: self.store.release(value),
                ]:
                    with self.assertRaises(ValueError):
                        operation()
        self.assertEqual(self.store.report(), before)

    def test_large_integer_quantities_remain_exact(self):
        quantity = 10 ** 30
        self.store.add_item("sku", quantity)
        self.store.add_item("sku", quantity)
        record = self.store.reserve("key", "sku", quantity + 1)
        self.assertEqual(Store(self.db).get_item("sku")["available"], quantity - 1)
        self.assertEqual(Store(self.db).report()["reserved_units"], quantity + 1)
        self.store.release(record["reservation_id"])
        self.assertEqual(self.store.get_item("sku")["available"], 2 * quantity)

    def test_connections_close_on_success_and_failure(self):
        original_connect = sqlite3.connect
        connections = []

        def connect(*args, **kwargs):
            connection = original_connect(*args, **kwargs)
            connections.append(connection)
            return connection

        with patch("reservation.store.sqlite3.connect", side_effect=connect):
            store = Store(self.db)
            store.add_item("sku", 2)
            store.get_item("sku")
            record = store.reserve("key", "sku", 2)
            store.release(record["reservation_id"])
            store.report()
            with self.assertRaises(Conflict):
                store.reserve("fail", "sku", 3)
            with self.assertRaises(NotFound):
                store.get_item("missing")
        self.assertEqual(len(connections), 8)
        for connection in connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

    def test_concurrent_adds_from_separate_instances(self):
        records = parallel(12, lambda _: Store(self.db).add_item("sku", 2))
        self.assertEqual(sorted(record["available"] for record in records), list(range(2, 25, 2)))
        self.assertEqual(self.store.get_item("sku")["available"], 24)

    def test_concurrent_reservations_do_not_oversell(self):
        self.store.add_item("sku", 5)

        def reserve(index):
            try:
                return Store(self.db).reserve(f"key-{index}", "sku", 1)
            except Conflict:
                return None

        records = [record for record in parallel(12, reserve) if record is not None]
        self.assertEqual(len(records), 5)
        self.assertEqual(len({record["reservation_id"] for record in records}), 5)
        self.assertEqual(self.store.report(), {
            "items": [{"sku": "sku", "available": 0}],
            "active_reservations": 5, "reserved_units": 5
        })

    def test_concurrent_retries_and_releases_are_exactly_once(self):
        self.store.add_item("sku", 3)
        records = parallel(12, lambda _: Store(self.db).reserve("key", "sku", 2))
        self.assertTrue(all(record == records[0] for record in records))
        self.assertEqual(self.store.get_item("sku")["available"], 1)
        released = parallel(12, lambda _: Store(self.db).release(records[0]["reservation_id"]))
        self.assertTrue(all(record == dict(records[0], status="released") for record in released))
        self.assertEqual(self.store.get_item("sku")["available"], 3)
        self.assertEqual(self.store.report()["active_reservations"], 0)

    def test_reports_are_consistent_during_writes(self):
        self.store.add_item("sku", 10)

        def write():
            store = Store(self.db)
            for index in range(30):
                record = store.reserve(f"key-{index}", "sku", 3)
                store.release(record["reservation_id"])

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(write)
            for _ in range(60):
                report = self.store.report()
                self.assertEqual(report["items"][0]["available"] + report["reserved_units"], 10)
                self.assertEqual(report["active_reservations"] * 3, report["reserved_units"])
            future.result(timeout=10)


class HTTPContractTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.server = self.start_server()

    def request(self, method, path, data=None, *, raw=None, headers=None):
        if raw is None and data is not None:
            raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=10)
        try:
            connection.request(method, path, body=raw, headers=headers or {})
            response = connection.getresponse()
            body = response.read()
            self.assertEqual(response.getheader("Content-Type"), "application/json")
            self.assertEqual(int(response.getheader("Content-Length")), len(body))
            result = json.loads(body)
            if response.status >= 400:
                self.assertEqual(set(result), {"error"})
                self.assertIsInstance(result["error"], str)
                self.assertTrue(result["error"])
            return response.status, result
        finally:
            connection.close()

    def test_health_and_empty_report(self):
        self.assertEqual(self.request("GET", "/health"), (200, {"ok": True}))
        self.assertEqual(self.request("GET", "/report"), (200, self.store.report()))

    def test_unicode_sku_and_url_encoded_delimiters(self):
        sku = "caf\u00e9 / \u96ea +?"
        record = {"sku": sku, "available": 5}
        self.assertEqual(self.request("POST", "/items", {"sku": f" {sku} ", "quantity": 5}), (201, record))
        self.assertEqual(self.request("GET", "/items/" + quote(sku, safe="")), (200, record))
        self.assertEqual(self.request("GET", "/report"), (200, self.store.report()))

    def test_reserve_retry_conflict_and_release(self):
        self.request("POST", "/items", {"sku": "sku", "quantity": 3})
        data = {"idempotency_key": "key", "sku": "sku", "quantity": 2}
        status, record = self.request("POST", "/reservations", data)
        self.assertEqual(status, 201)
        self.assertEqual(self.request("POST", "/reservations", data), (201, record))
        self.assertEqual(self.request("POST", "/reservations", dict(data, quantity=1))[0], 409)
        self.assertEqual(self.request("POST", "/reservations", dict(data, idempotency_key="other"))[0], 409)
        self.assertEqual(self.request("POST", "/reservations", dict(data, sku="missing", idempotency_key="missing"))[0], 404)
        release_path = f"/reservations/{record['reservation_id']}/release"
        released = dict(record, status="released")
        self.assertEqual(self.request("POST", release_path, {}), (200, released))
        self.assertEqual(self.request("POST", release_path, {}), (200, released))
        self.assertEqual(self.request("POST", "/reservations", data), (201, released))
        self.assertEqual(self.store.get_item("sku")["available"], 3)

    def test_invalid_json_missing_fields_and_values(self):
        for raw in [
            b"{", b"[]", b"null", b"true", b"42", b'"text"', b"\xff",
            b"{}", b'{"sku":"sku"}', b'{"sku":"sku","quantity":true}',
            b'{"sku":" ","quantity":1}', b'{"sku":"sku","quantity":0}',
            b'{"sku":"sku","quantity":1.5}', b'{"sku":"sku","quantity":"1"}',
            b'{"sku":"sku","quantity":1,"extra":NaN}', b"",
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(self.request("POST", "/items", raw=raw)[0], 400)
        for data in [{}, {"sku": "sku", "quantity": 1}, {"idempotency_key": "", "sku": "sku", "quantity": 1}]:
            self.assertEqual(self.request("POST", "/reservations", data)[0], 400)
        self.assertEqual(self.store.report()["items"], [])
        self.assertEqual(self.request("GET", "/health"), (200, {"ok": True}))

    def test_body_limit_before_reading_and_exact_boundary(self):
        self.assertEqual(self.request("POST", "/items", raw=b"", headers={"Content-Length": "65537"})[0], 413)
        self.assertEqual(self.request("POST", "/items", raw=b"", headers={"Content-Length": "9" * 100})[0], 413)
        body = b'{"sku":"limit","quantity":1}'
        body += b" " * (65536 - len(body))
        self.assertEqual(self.request("POST", "/items", raw=body), (201, {"sku": "limit", "available": 1}))
        self.assertEqual(self.request("GET", "/health"), (200, {"ok": True}))

    def test_invalid_framing_headers(self):
        for length in ["-1", "abc", "+2", "1.5"]:
            with self.subTest(length=length):
                self.assertEqual(self.request("POST", "/items", raw=b"{}", headers={"Content-Length": length})[0], 400)
        self.assertEqual(self.request("POST", "/items", raw=b"{}", headers={"Transfer-Encoding": "chunked"})[0], 400)

    def test_unknown_routes_missing_records_and_invalid_ids(self):
        for method, path in [
            ("GET", "/unknown"), ("POST", "/unknown"), ("GET", "/items/missing"),
            ("GET", "/items/"), ("DELETE", "/items/sku"),
            ("POST", "/reservations/999/release"),
        ]:
            with self.subTest(method=method, path=path):
                self.assertEqual(self.request(method, path, {} if method == "POST" else None)[0], 404)
        for value in ["0", "-1", "abc", "1.5"]:
            self.assertEqual(self.request("POST", f"/reservations/{value}/release", {})[0], 400)
        self.assertEqual(self.request("POST", "/reservations/1/release", [])[0], 400)
        self.assertEqual(self.request("GET", "/items/%FF")[0], 400)

    def test_concurrent_http_reservations_and_releases(self):
        self.store.add_item("sku", 5)
        results = parallel(12, lambda index: self.request("POST", "/reservations", {
            "idempotency_key": f"key-{index}", "sku": "sku", "quantity": 1
        }))
        records = [record for status, record in results if status == 201]
        self.assertEqual(len(records), 5)
        self.assertEqual(sum(status == 409 for status, _ in results), 7)
        self.assertEqual(self.store.report()["reserved_units"], 5)
        first = records[0]
        retried = parallel(12, lambda _: self.request("POST", "/reservations", {
            "idempotency_key": first["idempotency_key"], "sku": "sku", "quantity": 1
        }))
        self.assertTrue(all(result == (201, first) for result in retried))
        released = parallel(12, lambda _: self.request(
            "POST", f"/reservations/{first['reservation_id']}/release", {}
        ))
        self.assertTrue(all(result == (200, dict(first, status="released")) for result in released))
        self.assertEqual(self.store.get_item("sku")["available"], 1)
        self.assertEqual(self.store.report()["active_reservations"], 4)


class CLIContractTests(DatabaseTestCase):
    def command(self, *args, expected_code=0):
        result = subprocess.run(
            [sys.executable, "-m", "reservation", "--db", str(self.db), *args],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, expected_code, result.stderr)
        output = result.stdout if expected_code == 0 else result.stderr
        self.assertEqual(len(output.splitlines()), 1)
        self.assertEqual(result.stderr if expected_code == 0 else result.stdout, "")
        data = json.loads(output)
        if expected_code != 0:
            self.assertEqual(set(data), {"error"})
            self.assertIsInstance(data["error"], str)
        return data

    def test_cli_lifecycle_and_shared_store_http_state(self):
        self.assertEqual(self.command("add", "--sku", " sku ", "--quantity", "5"), {"sku": "sku", "available": 5})
        record = self.command("reserve", "--key", "key", "--sku", "sku", "--quantity", "2")
        self.assertEqual(self.command("reserve", "--key", "key", "--sku", "sku", "--quantity", "2"), record)
        server = self.start_server()
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        try:
            connection.request("POST", "/items", body=b'{"sku":"sku","quantity":3}')
            response = connection.getresponse()
            self.assertEqual(response.status, 201)
            self.assertEqual(json.loads(response.read()), {"sku": "sku", "available": 6})
        finally:
            connection.close()
        self.assertEqual(self.command("report"), self.store.report())
        self.assertEqual(self.command("release", "--id", str(record["reservation_id"])), dict(record, status="released"))
        self.assertEqual(Store(self.db).get_item("sku")["available"], 8)

    def test_domain_validation_and_parser_errors_are_json(self):
        self.store.add_item("sku", 1)
        for args in [
            ("add", "--sku", "sku", "--quantity", "0"),
            ("add", "--sku", " ", "--quantity", "1"),
            ("add", "--sku", "sku", "--quantity", "not-an-int"),
            ("add", "--sku", "sku"),
            ("reserve", "--key", "key", "--sku", "sku", "--quantity", "2"),
            ("reserve", "--key", "key", "--sku", "absent", "--quantity", "1"),
            ("release", "--id", "0"), ("release", "--id", "999"),
            ("serve", "--port", "65536"), ("unknown",), (),
        ]:
            with self.subTest(args=args):
                self.command(*args, expected_code=2)
        self.assertEqual(self.store.get_item("sku")["available"], 1)
        self.command("reserve", "--key", "key", "--sku", "sku", "--quantity", "1")
        self.command("reserve", "--key", "key", "--sku", "sku", "--quantity", "2", expected_code=2)

    def test_cli_serve_runs_http_and_blocks(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        process = subprocess.Popen(
            [sys.executable, "-m", "reservation", "--db", str(self.db),
             "serve", "--host", "127.0.0.1", "--port", str(port)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 5
            while True:
                self.assertIsNone(process.poll(), "CLI server exited before becoming ready")
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.2)
                try:
                    connection.request("GET", "/health")
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(json.loads(response.read()), {"ok": True})
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        self.fail("CLI server did not become ready")
                    time.sleep(0.01)
                finally:
                    connection.close()
            self.store.add_item("sku", 4)
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                connection.request("POST", "/reservations", body=json.dumps({
                    "idempotency_key": "serve-key", "sku": "sku", "quantity": 2
                }).encode("utf-8"))
                response = connection.getresponse()
                self.assertEqual(response.status, 201)
                self.assertEqual(json.loads(response.read())["status"], "active")
            finally:
                connection.close()
            self.assertEqual(self.command("report"), self.store.report())
            self.assertEqual(self.store.get_item("sku")["available"], 2)
            self.assertIsNone(process.poll())
        finally:
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
