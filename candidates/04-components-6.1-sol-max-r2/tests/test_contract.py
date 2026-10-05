"""Additional behavioral tests for persistence, concurrency, HTTP, and CLI."""

from concurrent.futures import ThreadPoolExecutor
import http.client
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.parse import quote

from reservation import Conflict, NotFound, Store
from reservation.http_api import create_server


ROOT = Path(__file__).resolve().parent.parent
UNSET = object()


class DatabaseCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix=".inventory-tests-", dir=ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "stock with spaces.db"
        self.store = Store(self.db)

    def concurrent(self, function, count=16):
        barrier = threading.Barrier(count)

        def run(index):
            barrier.wait(timeout=10)
            return function(index)

        with ThreadPoolExecutor(max_workers=count) as executor:
            futures = [executor.submit(run, index) for index in range(count)]
            return [future.result(timeout=30) for future in futures]


class StoreContractTests(DatabaseCase):
    def test_add_normalizes_and_persists_sorted_report(self):
        self.assertEqual(self.store.report(), {
            "items": [], "active_reservations": 0, "reserved_units": 0,
        })
        self.assertEqual(self.store.add_item(" z ", 4), {"sku": "z", "available": 4})
        self.store.add_item("a", 3)
        self.assertEqual(self.store.add_item("z", 2), {"sku": "z", "available": 6})
        reopened = Store(self.db)
        self.assertEqual(reopened.get_item("\t z\n"), {"sku": "z", "available": 6})
        self.assertEqual(reopened.report(), {
            "items": [{"sku": "a", "available": 3}, {"sku": "z", "available": 6}],
            "active_reservations": 0, "reserved_units": 0,
        })

    def test_retries_and_releases_survive_reopening(self):
        self.store.add_item("sku", 8)
        record = self.store.reserve(" key ", " sku ", 3)
        self.assertEqual(record, {
            "reservation_id": record["reservation_id"],
            "idempotency_key": "key", "sku": "sku", "quantity": 3, "status": "active",
        })
        self.assertIs(type(record["reservation_id"]), int)
        self.assertGreater(record["reservation_id"], 0)
        reopened = Store(self.db)
        self.assertEqual(reopened.reserve("key", "sku", 3), record)
        self.assertEqual(reopened.report()["reserved_units"], 3)
        released = reopened.release(record["reservation_id"])
        self.assertEqual(released, dict(record, status="released"))
        reopened = Store(self.db)
        self.assertEqual(reopened.release(record["reservation_id"]), released)
        self.assertEqual(reopened.reserve("key", "sku", 3), released)
        self.assertEqual(reopened.get_item("sku")["available"], 8)
        self.assertEqual(reopened.report()["active_reservations"], 0)

    def test_failures_leave_stock_and_keys_unchanged(self):
        self.store.add_item("sku", 4)
        self.store.reserve("existing", "sku", 2)
        before = self.store.report()
        for key, sku, quantity, error in [
            ("existing", "sku", 3, Conflict),
            ("existing", "missing", 2, Conflict),
            ("new", "sku", 3, Conflict),
            ("missing-key", "missing", 1, NotFound),
        ]:
            with self.subTest(key=key, sku=sku, quantity=quantity):
                with self.assertRaises(error):
                    self.store.reserve(key, sku, quantity)
                self.assertEqual(self.store.report(), before)
        with self.assertRaises(NotFound):
            self.store.get_item("missing")
        with self.assertRaises(NotFound):
            self.store.release(999)
        with self.assertRaises(NotFound):
            self.store.release(1 << 80)
        self.store.reserve("new", "sku", 1)
        self.store.add_item("missing", 1)
        self.store.reserve("missing-key", "missing", 1)
        self.assertEqual(self.store.report()["active_reservations"], 3)

    def test_argument_validation_has_no_side_effects(self):
        self.store.add_item("sku", 10)
        before = self.store.report()
        for bad in [None, True, False, 1, b"sku", [], {}, "", " \t\n"]:
            for function, args in [
                (self.store.add_item, (bad, 1)),
                (self.store.get_item, (bad,)),
                (self.store.reserve, (bad, "sku", 1)),
                (self.store.reserve, ("key", bad, 1)),
            ]:
                with self.subTest(function=function.__name__, value=bad):
                    with self.assertRaises(ValueError):
                        function(*args)
        for bad in [None, True, False, 0, -1, 1.0, "1", [], {}, float("inf")]:
            for function, args in [
                (self.store.add_item, ("sku", bad)),
                (self.store.reserve, ("key", "sku", bad)),
                (self.store.release, (bad,)),
            ]:
                with self.subTest(function=function.__name__, value=bad):
                    with self.assertRaises(ValueError):
                        function(*args)
        self.assertEqual(self.store.report(), before)

    def test_large_integer_stock_remains_exact(self):
        quantity = 10 ** 80
        self.store.add_item("sku", quantity)
        self.store.add_item("sku", 9)
        record = self.store.reserve("large", "sku", quantity)
        self.assertEqual(Store(self.db).get_item("sku")["available"], 9)
        self.assertEqual(self.store.report()["reserved_units"], quantity)
        self.store.release(record["reservation_id"])
        self.assertEqual(self.store.get_item("sku")["available"], quantity + 9)

    def test_concurrent_initialization_and_additions(self):
        new_db = Path(self.tmp.name) / "new.db"

        def add(index):
            return Store(new_db).add_item("sku", 1)

        results = self.concurrent(add, count=20)
        self.assertEqual(sorted(record["available"] for record in results), list(range(1, 21)))
        self.assertEqual(Store(new_db).get_item("sku")["available"], 20)

    def test_concurrent_reservations_cannot_oversell(self):
        self.store.add_item("sku", 7)

        def reserve(index):
            try:
                return Store(self.db).reserve(f"key-{index}", "sku", 1)
            except Conflict:
                return None

        results = self.concurrent(reserve, count=20)
        successes = [record for record in results if record is not None]
        self.assertEqual(len(successes), 7)
        self.assertEqual(len({record["reservation_id"] for record in successes}), 7)
        self.assertEqual(self.store.report(), {
            "items": [{"sku": "sku", "available": 0}],
            "active_reservations": 7, "reserved_units": 7,
        })

    def test_concurrent_identical_retries_and_releases(self):
        self.store.add_item("sku", 10)
        records = self.concurrent(lambda index: Store(self.db).reserve("same", "sku", 4))
        self.assertTrue(all(record == records[0] for record in records))
        self.assertEqual(self.store.get_item("sku")["available"], 6)
        self.assertEqual(self.store.report()["active_reservations"], 1)
        reservation_id = records[0]["reservation_id"]
        released = self.concurrent(lambda index: Store(self.db).release(reservation_id))
        self.assertTrue(all(record == dict(records[0], status="released") for record in released))
        self.assertEqual(self.store.get_item("sku")["available"], 10)
        self.assertEqual(self.store.report()["reserved_units"], 0)

    def test_concurrent_key_parameter_conflict(self):
        self.store.add_item("sku", 100)

        def reserve(index):
            try:
                return Store(self.db).reserve("same", "sku", 2 + index % 2)
            except Conflict:
                return None

        results = self.concurrent(reserve)
        successes = [record for record in results if record is not None]
        self.assertEqual(len(successes), 8)
        self.assertTrue(all(record == successes[0] for record in successes))
        self.assertEqual(self.store.get_item("sku")["available"], 100 - successes[0]["quantity"])
        self.assertEqual(self.store.report()["active_reservations"], 1)

    def test_reports_use_one_consistent_snapshot(self):
        self.store.add_item("sku", 10)
        barrier = threading.Barrier(2)

        def mutate():
            store = Store(self.db)
            barrier.wait(timeout=10)
            for index in range(40):
                record = store.reserve(f"key-{index}", "sku", 3)
                store.release(record["reservation_id"])

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(mutate)
            barrier.wait(timeout=10)
            for _ in range(100):
                report = self.store.report()
                self.assertEqual(report["items"][0]["available"] + report["reserved_units"], 10)
                self.assertIn(report["active_reservations"], (0, 1))
            future.result(timeout=30)


class HTTPContractTests(DatabaseCase):
    def setUp(self):
        super().setUp()
        self.server = create_server(self.db)
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.assertFalse(self.thread.is_alive())

    def request(self, method, path, record=UNSET, *, raw=UNSET, headers=None):
        if raw is not UNSET:
            body = raw
        elif record is not UNSET:
            body = json.dumps(record, ensure_ascii=False).encode("utf-8")
        else:
            body = None
        request_headers = {"Content-Type": "application/json"}
        request_headers.update(headers or {})
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=10)
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            data = response.read()
            self.assertEqual(response.getheader("Content-Type"), "application/json")
            self.assertEqual(int(response.getheader("Content-Length")), len(data))
            return response.status, json.loads(data.decode("utf-8"))
        finally:
            connection.close()

    def assert_error(self, status, method, path, record=UNSET, **kwargs):
        actual, result = self.request(method, path, record, **kwargs)
        self.assertEqual(actual, status)
        self.assertEqual(set(result), {"error"})
        self.assertIsInstance(result["error"], str)
        self.assertTrue(result["error"])

    def test_full_lifecycle_and_encoded_unicode_sku(self):
        self.assertEqual(self.request("GET", "/health"), (200, {"ok": True}))
        sku = "café/工具 +?#%"
        self.assertEqual(self.request("POST", "/items", {"sku": f" {sku} ", "quantity": 5}),
                         (201, {"sku": sku, "available": 5}))
        self.assertEqual(self.request("GET", "/items/" + quote(sku, safe="")),
                         (200, {"sku": sku, "available": 5}))
        request = {"idempotency_key": "order", "sku": sku, "quantity": 2}
        status, record = self.request("POST", "/reservations", request)
        self.assertEqual(status, 201)
        self.assertEqual(record["status"], "active")
        self.assertEqual(self.request("POST", "/reservations", request), (201, record))
        self.assert_error(409, "POST", "/reservations", dict(request, quantity=3))
        self.assert_error(409, "POST", "/reservations", dict(request, idempotency_key="new", quantity=4))
        self.assertEqual(self.request("GET", "/report"), (200, {
            "items": [{"sku": sku, "available": 3}],
            "active_reservations": 1, "reserved_units": 2,
        }))
        released = dict(record, status="released")
        path = f"/reservations/{record['reservation_id']}/release"
        self.assertEqual(self.request("POST", path, {}), (200, released))
        self.assertEqual(self.request("POST", path, {}), (200, released))
        self.assertEqual(self.request("POST", "/reservations", request), (201, released))
        self.assertEqual(self.store.get_item(sku)["available"], 5)

    def test_unknown_routes_and_missing_records_are_json(self):
        for method, path, record in [
            ("GET", "/unknown", UNSET),
            ("GET", "/items/missing", UNSET),
            ("GET", "/items/sku/extra", UNSET),
            ("POST", "/unknown", {}),
            ("POST", "/reservations/999/release", {}),
            ("POST", "/reservations", {"idempotency_key": "k", "sku": "missing", "quantity": 1}),
            ("PUT", "/items", {}),
            ("DELETE", "/items/missing", UNSET),
        ]:
            with self.subTest(method=method, path=path):
                self.assert_error(404, method, path, record)

    def test_malformed_json_and_objects_do_not_stop_server(self):
        for body in [
            b"", b"{", b"[]", b"null", b'"text"', b"1", b"true", b"\xff",
            b'{"sku":"s","quantity":NaN}',
            b'{"sku":"s","quantity":Infinity}',
            b'{"sku":"s","quantity":1,"padding":-Infinity}',
            b'{"sku":"s","quantity":1,"padding":' + b'[' * 1100 + b'0',
        ]:
            with self.subTest(body=body[:50]):
                self.assert_error(400, "POST", "/items", raw=body)
                self.assertEqual(self.request("GET", "/health"), (200, {"ok": True}))
        for path, record in [
            ("/items", {}),
            ("/items", {"sku": "s"}),
            ("/items", {"quantity": 1}),
            ("/reservations", {"sku": "s", "quantity": 1}),
            ("/reservations", {"idempotency_key": "k", "quantity": 1}),
            ("/reservations", {"idempotency_key": "k", "sku": "s"}),
            ("/reservations/1/release", []),
        ]:
            with self.subTest(path=path, record=record):
                self.assert_error(400, "POST", path, record)
        self.assertEqual(self.store.report()["items"], [])

    def test_invalid_values_are_bad_requests(self):
        self.store.add_item("sku", 5)
        for bad in [True, False, 0, -1, 1.0, "1", None, [], {}]:
            with self.subTest(quantity=bad):
                self.assert_error(400, "POST", "/items", {"sku": "sku", "quantity": bad})
                self.assert_error(400, "POST", "/reservations", {
                    "idempotency_key": "k", "sku": "sku", "quantity": bad,
                })
        for bad in [True, 1, None, [], {}, "", "  "]:
            with self.subTest(string=bad):
                self.assert_error(400, "POST", "/items", {"sku": bad, "quantity": 1})
                self.assert_error(400, "POST", "/reservations", {
                    "idempotency_key": bad, "sku": "sku", "quantity": 1,
                })
        for bad in ["0", "-1", "true", "1.5", "bad"]:
            with self.subTest(reservation_id=bad):
                self.assert_error(400, "POST", f"/reservations/{bad}/release", {})
        self.assert_error(400, "GET", "/items/%20")
        self.assert_error(400, "GET", "/items/%FF")
        self.assertEqual(self.store.get_item("sku")["available"], 5)
        self.assertEqual(self.store.report()["active_reservations"], 0)

    def test_body_limit_includes_exact_boundary(self):
        prefix = b'{"sku":"bounded","quantity":1,"padding":"'
        suffix = b'"}'
        body = prefix + b'x' * (65536 - len(prefix) - len(suffix)) + suffix
        self.assertEqual(len(body), 65536)
        self.assertEqual(self.request("POST", "/items", raw=body),
                         (201, {"sku": "bounded", "available": 1}))
        self.assert_error(413, "POST", "/items", raw=body + b" ")
        self.assert_error(413, "POST", "/items", raw=b"", headers={"Content-Length": "65537"})
        self.assert_error(413, "POST", "/items", raw=b"", headers={"Content-Length": "9" * 1000})
        self.assertEqual(self.request("GET", "/health"), (200, {"ok": True}))
        self.assertEqual(self.store.get_item("bounded")["available"], 1)

    def test_invalid_content_lengths_and_truncated_body(self):
        for value in ["-1", "abc", "1.5", ""]:
            with self.subTest(value=value):
                self.assert_error(400, "POST", "/items", raw=b"", headers={"Content-Length": value})
        self.assert_error(400, "POST", "/items", raw=b"", headers={"Transfer-Encoding": "chunked"})
        with socket.create_connection(self.server.server_address, timeout=5) as connection:
            connection.sendall(b"POST /items HTTP/1.1\r\nHost: localhost\r\nContent-Length: 10\r\n\r\n{}")
            connection.shutdown(socket.SHUT_WR)
            response = http.client.HTTPResponse(connection)
            response.begin()
            self.assertEqual(response.status, 400)
            self.assertIn("error", json.loads(response.read()))
        self.assertEqual(self.request("GET", "/health"), (200, {"ok": True}))

    def test_concurrent_http_requests_obey_stock_limits(self):
        self.store.add_item("sku", 5)
        results = self.concurrent(lambda index: self.request("POST", "/reservations", {
            "idempotency_key": f"key-{index}", "sku": "sku", "quantity": 1,
        }), count=12)
        self.assertEqual(sum(status == 201 for status, record in results), 5)
        self.assertEqual(sum(status == 409 for status, record in results), 7)
        self.assertEqual(self.store.report(), {
            "items": [{"sku": "sku", "available": 0}],
            "active_reservations": 5, "reserved_units": 5,
        })

    def test_concurrent_http_retries_and_release_once(self):
        self.store.add_item("sku", 5)
        request = {"idempotency_key": "same", "sku": "sku", "quantity": 2}
        results = self.concurrent(lambda index: self.request("POST", "/reservations", request), count=12)
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(results[0][0], 201)
        record = results[0][1]
        self.assertEqual(self.store.get_item("sku")["available"], 3)
        self.assertEqual(self.store.report()["active_reservations"], 1)
        path = f"/reservations/{record['reservation_id']}/release"
        results = self.concurrent(lambda index: self.request("POST", path, {}), count=12)
        self.assertTrue(all(result == (200, dict(record, status="released")) for result in results))
        self.assertEqual(self.store.get_item("sku")["available"], 5)
        self.assertEqual(self.store.report()["reserved_units"], 0)


class CLIContractTests(DatabaseCase):
    def cli(self, *args, success=True):
        result = subprocess.run(
            [sys.executable, "-m", "reservation", "--db", str(self.db), *args],
            cwd=ROOT, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0 if success else 2, result.stderr)
        self.assertEqual(result.stderr if success else result.stdout, "")
        output = result.stdout if success else result.stderr
        self.assertEqual(len(output.splitlines()), 1)
        record = json.loads(output)
        if not success:
            self.assertEqual(set(record), {"error"})
            self.assertIsInstance(record["error"], str)
        return record

    def test_cli_commands_share_persistent_store_state(self):
        self.assertEqual(self.cli("add", "--sku", " sku ", "--quantity", "4"),
                         {"sku": "sku", "available": 4})
        self.assertEqual(self.cli("add", "--sku", "sku", "--quantity", "2"),
                         {"sku": "sku", "available": 6})
        record = self.cli("reserve", "--key", "key", "--sku", "sku", "--quantity", "3")
        self.assertEqual(self.store.get_item("sku")["available"], 3)
        self.assertEqual(self.cli("reserve", "--key", "key", "--sku", "sku", "--quantity", "3"), record)
        released = self.cli("release", "--id", str(record["reservation_id"]))
        self.assertEqual(released, dict(record, status="released"))
        self.assertEqual(self.cli("release", "--id", str(record["reservation_id"])), released)
        self.assertEqual(self.cli("reserve", "--key", "key", "--sku", "sku", "--quantity", "3"), released)
        self.assertEqual(self.cli("report"), {
            "items": [{"sku": "sku", "available": 6}],
            "active_reservations": 0, "reserved_units": 0,
        })

    def test_cli_errors_are_single_json_records(self):
        self.store.add_item("sku", 1)
        self.store.reserve("key", "sku", 1)
        for args in [
            ("add", "--sku", "sku", "--quantity", "0"),
            ("add", "--sku", "sku", "--quantity", "-1"),
            ("add", "--sku", "sku", "--quantity", "1.0"),
            ("add", "--sku", "sku", "--quantity", "true"),
            ("add", "--sku", " ", "--quantity", "1"),
            ("add", "--sku", "sku"),
            ("reserve", "--key", "other", "--sku", "sku", "--quantity", "1"),
            ("reserve", "--key", "key", "--sku", "sku", "--quantity", "2"),
            ("reserve", "--key", "other", "--sku", "missing", "--quantity", "1"),
            ("reserve", "--key", " ", "--sku", "sku", "--quantity", "1"),
            ("release", "--id", "999"),
            ("release", "--id", "0"),
            ("release", "--id", "bad"),
            ("unknown",),
            ("serve", "--port", "-1"),
            ("serve", "--port", "65536"),
        ]:
            with self.subTest(args=args):
                self.cli(*args, success=False)
        self.assertEqual(self.store.get_item("sku")["available"], 0)
        self.assertEqual(self.store.report()["active_reservations"], 1)

    def test_cli_serve_shares_state_with_http_and_cli(self):
        self.cli("add", "--sku", "sku", "--quantity", "3")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        process = subprocess.Popen(
            [sys.executable, "-m", "reservation", "--db", str(self.db),
             "serve", "--host", "127.0.0.1", "--port", str(port)],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 10
            while True:
                self.assertIsNone(process.poll())
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                try:
                    connection.request("GET", "/health")
                    response = connection.getresponse()
                    self.assertEqual(json.loads(response.read()), {"ok": True})
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        self.fail("CLI server did not become ready")
                    time.sleep(0.02)
                finally:
                    connection.close()
            self.assertIsNone(process.poll())
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                connection.request("POST", "/reservations", body=json.dumps({
                    "idempotency_key": "http-key", "sku": "sku", "quantity": 2,
                }))
                response = connection.getresponse()
                self.assertEqual(response.status, 201)
                record = json.loads(response.read())
            finally:
                connection.close()
            self.assertEqual(self.cli("report")["reserved_units"], 2)
            self.assertEqual(self.store.get_item("sku")["available"], 1)
            self.cli("release", "--id", str(record["reservation_id"]))
            self.assertEqual(self.store.get_item("sku")["available"], 3)
        finally:
            process.terminate()
            stdout, stderr = process.communicate(timeout=5)
        self.assertEqual(stdout, b"")
        self.assertEqual(stderr, b"")


if __name__ == "__main__":
    unittest.main()
