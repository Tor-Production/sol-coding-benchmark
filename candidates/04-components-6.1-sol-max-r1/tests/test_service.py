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
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

from reservation import Conflict, NotFound, Store
from reservation.http_api import MAX_BODY_BYTES, create_server


ROOT = Path(__file__).resolve().parents[1]


def concurrent_calls(count, operation):
    barrier = threading.Barrier(count)

    def run(index):
        barrier.wait(timeout=10)
        return operation(index)

    with ThreadPoolExecutor(max_workers=count) as executor:
        return list(executor.map(run, range(count)))


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "stock.db"
        self.store = Store(self.path)


class StoreTests(DatabaseTests):
    def test_add_normalizes_and_persists(self):
        self.assertEqual(self.store.report(), {
            "items": [], "active_reservations": 0, "reserved_units": 0,
        })
        self.assertEqual(self.store.add_item("  sku\t", 3), {"sku": "sku", "available": 3})
        self.assertEqual(Store(self.path).add_item("sku", 4), {"sku": "sku", "available": 7})
        self.assertEqual(Store(self.path).get_item(" sku "), {"sku": "sku", "available": 7})

    def test_retry_and_release_persist_without_changing_stock_twice(self):
        self.store.add_item("sku", 5)
        original = self.store.reserve(" key ", " sku ", 2)
        self.assertEqual(original, {
            "reservation_id": 1, "idempotency_key": "key", "sku": "sku",
            "quantity": 2, "status": "active",
        })
        reopened = Store(self.path)
        self.assertEqual(reopened.reserve("key", "sku", 2), original)
        self.assertEqual(reopened.get_item("sku")["available"], 3)
        released = reopened.release(original["reservation_id"])
        self.assertEqual(released, dict(original, status="released"))
        self.assertEqual(Store(self.path).release(original["reservation_id"]), released)
        self.assertEqual(Store(self.path).reserve("key", "sku", 2), released)
        self.assertEqual(self.store.report(), {
            "items": [{"sku": "sku", "available": 5}],
            "active_reservations": 0, "reserved_units": 0,
        })

    def test_failures_do_not_use_keys_or_change_stock(self):
        self.store.add_item("sku", 2)
        before = self.store.report()
        with self.assertRaises(Conflict):
            self.store.reserve("key", "sku", 3)
        with self.assertRaises(NotFound):
            self.store.reserve("key", "missing", 1)
        with self.assertRaises(NotFound):
            self.store.get_item("missing")
        with self.assertRaises(NotFound):
            self.store.release(999)
        with self.assertRaises(NotFound):
            self.store.release(10 ** 40)
        self.assertEqual(self.store.report(), before)
        self.assertEqual(self.store.reserve("key", "sku", 2)["reservation_id"], 1)

    def test_key_conflicts_before_and_after_release(self):
        self.store.add_item("sku", 5)
        record = self.store.reserve("key", "sku", 2)
        for released in (False, True):
            if released:
                self.store.release(record["reservation_id"])
            before = self.store.report()
            for sku, quantity in (("sku", 1), ("missing", 2)):
                with self.subTest(released=released, sku=sku, quantity=quantity):
                    with self.assertRaises(Conflict):
                        self.store.reserve(" key ", sku, quantity)
                    self.assertEqual(self.store.report(), before)

    def test_validation_does_not_mutate_state(self):
        self.store.add_item("sku", 5)
        before = self.store.report()
        for value in (None, True, 1, [], {}, "", " \t\n"):
            with self.subTest(string=value):
                for call in (
                    lambda: self.store.add_item(value, 1),
                    lambda: self.store.get_item(value),
                    lambda: self.store.reserve(value, "sku", 1),
                    lambda: self.store.reserve("key", value, 1),
                ):
                    with self.assertRaises(ValueError):
                        call()
        for value in (None, True, False, 0, -1, 1.0, "1", [], {}):
            with self.subTest(integer=value):
                for call in (
                    lambda: self.store.add_item("sku", value),
                    lambda: self.store.reserve("key", "sku", value),
                    lambda: self.store.release(value),
                ):
                    with self.assertRaises(ValueError):
                        call()
        self.assertEqual(self.store.report(), before)

    def test_report_sorts_items_and_counts_only_active_units(self):
        for sku, units in (("z", 8), ("a", 4), ("m", 3)):
            self.store.add_item(sku, units)
        self.store.reserve("z-key", "z", 2)
        self.store.reserve("a-key", "a", 1)
        released = self.store.reserve("m-key", "m", 3)
        self.store.release(released["reservation_id"])
        self.assertEqual(self.store.report(), {
            "items": [{"sku": "a", "available": 3},
                      {"sku": "m", "available": 3},
                      {"sku": "z", "available": 6}],
            "active_reservations": 2, "reserved_units": 3,
        })

    def test_large_positive_quantities_remain_exact(self):
        units = 10 ** 40
        self.store.add_item("sku", units)
        self.store.add_item("sku", units)
        record = self.store.reserve("key", "sku", units + 1)
        self.assertEqual(Store(self.path).report(), {
            "items": [{"sku": "sku", "available": units - 1}],
            "active_reservations": 1, "reserved_units": units + 1,
        })
        self.store.release(record["reservation_id"])
        self.assertEqual(self.store.get_item("sku")["available"], units * 2)

    def test_reservation_insert_failure_rolls_back_stock_update(self):
        self.store.add_item("sku", 5)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("""CREATE TRIGGER fail_insert BEFORE INSERT ON reservations
                BEGIN SELECT RAISE(ABORT, 'injected failure'); END""")
            connection.commit()
        before = self.store.report()
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.reserve("key", "sku", 2)
        self.assertEqual(self.store.report(), before)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("DROP TRIGGER fail_insert")
            connection.commit()
        self.assertEqual(self.store.reserve("key", "sku", 2)["reservation_id"], 1)

    def test_connections_close_after_success_and_failure(self):
        self.store.add_item("sku", 2)
        record = self.store.reserve("key", "sku", 1)
        with self.assertRaises(Conflict):
            self.store.reserve("other", "sku", 2)
        self.store.get_item("sku")
        self.store.release(record["reservation_id"])
        before = self.store.report()
        moved = self.path.with_suffix(".moved.db")
        self.path.rename(moved)  # Windows rejects this if a connection is left open.
        self.assertEqual(Store(moved).report(), before)

    def test_concurrent_initialization_and_adds(self):
        fresh = Path(self.tmp.name) / "new.db"
        results = concurrent_calls(12, lambda _: Store(fresh).add_item("sku", 1))
        self.assertEqual(sorted(record["available"] for record in results), list(range(1, 13)))
        self.assertEqual(Store(fresh).get_item("sku")["available"], 12)

    def test_concurrent_reservations_do_not_oversell(self):
        self.store.add_item("sku", 5)

        def reserve(index):
            try:
                return Store(self.path).reserve(f"key-{index}", "sku", 1)
            except Conflict:
                return None

        records = [record for record in concurrent_calls(12, reserve) if record is not None]
        self.assertEqual(len(records), 5)
        self.assertEqual(len({record["reservation_id"] for record in records}), 5)
        self.assertEqual(self.store.report(), {
            "items": [{"sku": "sku", "available": 0}],
            "active_reservations": 5, "reserved_units": 5,
        })

    def test_concurrent_identical_retries_and_releases(self):
        self.store.add_item("sku", 5)
        records = concurrent_calls(12, lambda _: Store(self.path).reserve("key", "sku", 2))
        self.assertTrue(all(record == records[0] for record in records))
        self.assertEqual(self.store.get_item("sku")["available"], 3)
        self.assertEqual(self.store.report()["active_reservations"], 1)
        reservation_id = records[0]["reservation_id"]
        released = concurrent_calls(12, lambda _: Store(self.path).release(reservation_id))
        self.assertTrue(all(record == released[0] for record in released))
        self.assertEqual(released[0]["status"], "released")
        retried = concurrent_calls(12, lambda _: Store(self.path).reserve("key", "sku", 2))
        self.assertTrue(all(record == released[0] for record in retried))
        self.assertEqual(self.store.get_item("sku")["available"], 5)

    def test_concurrent_reports_use_a_consistent_snapshot(self):
        self.store.add_item("sku", 10)

        def operate(index):
            store = Store(self.path)
            if index == 0:
                for number in range(60):
                    record = store.reserve(f"key-{number}", "sku", 3)
                    store.release(record["reservation_id"])
            else:
                for _ in range(120):
                    report = store.report()
                    self.assertEqual(report["items"][0]["available"] + report["reserved_units"], 10)
                    self.assertEqual(report["reserved_units"], report["active_reservations"] * 3)

        concurrent_calls(3, operate)


class HttpTests(DatabaseTests):
    def setUp(self):
        super().setUp()
        self.server = create_server(self.path)
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        self.assertFalse(self.thread.is_alive())

    def request(self, method, path, record=None, *, body=None, headers=None):
        if body is None and record is not None:
            body = json.dumps(record, ensure_ascii=False).encode("utf-8")
        request_headers = {"Content-Type": "application/json"}
        request_headers.update(headers or {})
        with closing(http.client.HTTPConnection(*self.server.server_address, timeout=10)) as connection:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            raw = response.read()
            self.assertEqual(response.getheader("Content-Type"), "application/json")
            self.assertEqual(int(response.getheader("Content-Length")), len(raw))
            result = json.loads(raw.decode("utf-8"))
            if response.status >= 400:
                self.assertEqual(set(result), {"error"})
                self.assertIsInstance(result["error"], str)
            return response.status, result

    def test_http_lifecycle_with_encoded_unicode_sku(self):
        self.assertEqual(self.request("GET", "/health"), (200, {"ok": True}))
        sku = "café / 雪+%"
        self.assertEqual(self.request("POST", "/items", {"sku": f" {sku} ", "quantity": 5}),
                         (201, {"sku": sku, "available": 5}))
        self.assertEqual(self.request("GET", "/items/" + quote(sku, safe="")),
                         (200, {"sku": sku, "available": 5}))
        body = {"idempotency_key": " key ", "sku": sku, "quantity": 2}
        status, original = self.request("POST", "/reservations", body)
        self.assertEqual(status, 201)
        self.assertEqual(original["idempotency_key"], "key")
        self.assertEqual(self.request("POST", "/reservations", body), (201, original))
        route = f"/reservations/{original['reservation_id']}/release"
        released = dict(original, status="released")
        self.assertEqual(self.request("POST", route, {}), (200, released))
        self.assertEqual(self.request("POST", route, {}), (200, released))
        self.assertEqual(self.request("POST", "/reservations", body), (201, released))
        self.assertEqual(self.request("GET", "/report"), (200, self.store.report()))
        self.assertEqual(self.store.get_item(sku)["available"], 5)

    def test_bad_json_and_fields_return_400_and_server_keeps_running(self):
        for raw in (b"", b"{", b"[]", b"null", b'"text"', b"true", b"3", b"\xff",
                    b'{"sku":"sku","quantity":NaN}', b"[" * 1100):
            with self.subTest(body=raw[:40]):
                self.assertEqual(self.request("POST", "/items", body=raw)[0], 400)
        for body in ({}, {"sku": "sku"}, {"quantity": 1}, {"sku": None, "quantity": 1},
                     {"sku": " ", "quantity": 1}, {"sku": "sku", "quantity": True},
                     {"sku": "sku", "quantity": 0}, {"sku": "sku", "quantity": -1},
                     {"sku": "sku", "quantity": 1.0}, {"sku": "sku", "quantity": "1"}):
            with self.subTest(body=body):
                self.assertEqual(self.request("POST", "/items", body)[0], 400)
        self.assertEqual(self.request("POST", "/reservations", {})[0], 400)
        self.assertEqual(self.request("POST", "/reservations", {
            "idempotency_key": " ", "sku": "sku", "quantity": 1,
        })[0], 400)
        self.assertEqual(self.store.report()["items"], [])
        self.assertEqual(self.request("GET", "/health"), (200, {"ok": True}))

    def test_not_found_conflict_and_unknown_routes(self):
        self.store.add_item("sku", 2)
        self.assertEqual(self.request("GET", "/items/missing")[0], 404)
        self.assertEqual(self.request("POST", "/reservations", {
            "idempotency_key": "key", "sku": "missing", "quantity": 1,
        })[0], 404)
        self.assertEqual(self.request("POST", "/reservations", {
            "idempotency_key": "key", "sku": "sku", "quantity": 3,
        })[0], 409)
        self.store.reserve("key", "sku", 1)
        before = self.store.report()
        self.assertEqual(self.request("POST", "/reservations", {
            "idempotency_key": "key", "sku": "sku", "quantity": 2,
        })[0], 409)
        self.assertEqual(self.request("POST", "/reservations/999/release", {})[0], 404)
        for reservation_id in ("0", "-1", "abc", "1.0"):
            self.assertEqual(self.request("POST", f"/reservations/{reservation_id}/release", {})[0], 400)
        for method, path in (("GET", "/unknown"), ("POST", "/unknown"),
                             ("GET", "/items"), ("GET", "/items/a/b"),
                             ("POST", "/report"), ("PUT", "/items"),
                             ("WHATEVER", "/unknown")):
            self.assertEqual(self.request(method, path, {})[0], 404)
        self.assertEqual(self.request("GET", "/items/%FF")[0], 400)
        self.assertEqual(self.request("GET", "/items/%ZZ")[0], 400)
        self.assertEqual(self.store.report(), before)

    def test_release_requires_a_json_object(self):
        self.store.add_item("sku", 2)
        record = self.store.reserve("key", "sku", 1)
        route = f"/reservations/{record['reservation_id']}/release"
        for raw in (b"", b"null", b"[]", b"{"):
            self.assertEqual(self.request("POST", route, body=raw)[0], 400)
        self.assertEqual(self.store.get_item("sku")["available"], 1)
        self.assertEqual(self.request("POST", route, {})[0], 200)

    def test_body_limit_and_invalid_framing(self):
        self.assertEqual(self.request("POST", "/items", body=b"", headers={
            "Content-Length": str(MAX_BODY_BYTES + 1),
        })[0], 413)
        record = b'{"sku":"sku","quantity":1}'
        at_limit = record + b" " * (MAX_BODY_BYTES - len(record))
        self.assertEqual(self.request("POST", "/items", body=at_limit),
                         (201, {"sku": "sku", "available": 1}))
        self.assertEqual(self.request("POST", "/items", body=at_limit + b" ")[0], 413)
        for length in ("-1", "abc", ""):
            self.assertEqual(self.request("POST", "/items", body=b"", headers={
                "Content-Length": length,
            })[0], 400)
        self.assertEqual(self.request("POST", "/items", body=b"", headers={
            "Transfer-Encoding": "chunked",
        })[0], 400)
        self.assertEqual(self.store.get_item("sku")["available"], 1)
        self.assertEqual(self.request("GET", "/health")[0], 200)

    def test_concurrent_http_reservations_do_not_oversell(self):
        self.store.add_item("sku", 5)
        results = concurrent_calls(12, lambda index: self.request("POST", "/reservations", {
            "idempotency_key": f"key-{index}", "sku": "sku", "quantity": 1,
        }))
        self.assertEqual(sum(status == 201 for status, _ in results), 5)
        self.assertEqual(sum(status == 409 for status, _ in results), 7)
        self.assertEqual(self.store.report(), {
            "items": [{"sku": "sku", "available": 0}],
            "active_reservations": 5, "reserved_units": 5,
        })

    def test_concurrent_http_identical_keys_create_one_reservation(self):
        self.store.add_item("sku", 5)
        results = concurrent_calls(12, lambda _: self.request("POST", "/reservations", {
            "idempotency_key": "key", "sku": "sku", "quantity": 2,
        }))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(results[0][0], 201)
        self.assertEqual(self.store.report(), {
            "items": [{"sku": "sku", "available": 3}],
            "active_reservations": 1, "reserved_units": 2,
        })


class CliTests(DatabaseTests):
    def cli(self, *arguments, expected_status=0):
        result = subprocess.run(
            [sys.executable, "-m", "reservation", "--db", str(self.path), *arguments],
            cwd=ROOT, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, expected_status, result.stderr)
        if expected_status == 0:
            self.assertEqual(result.stderr, "")
            self.assertEqual(len(result.stdout.splitlines()), 1)
            return json.loads(result.stdout)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(result.stderr.splitlines()), 1)
        record = json.loads(result.stderr)
        self.assertEqual(set(record), {"error"})
        self.assertIsInstance(record["error"], str)
        return record

    def test_finite_commands_share_state_with_store(self):
        sku = "café"
        self.assertEqual(self.cli("add", "--sku", sku, "--quantity", "5"),
                         {"sku": sku, "available": 5})
        self.assertEqual(self.store.get_item(sku)["available"], 5)
        arguments = ("reserve", "--key", "key", "--sku", sku, "--quantity", "2")
        record = self.cli(*arguments)
        self.assertEqual(self.cli(*arguments), record)
        released = self.cli("release", "--id", str(record["reservation_id"]))
        self.assertEqual(released, dict(record, status="released"))
        self.assertEqual(self.cli(*arguments), released)
        self.assertEqual(self.cli("report"), Store(self.path).report())

    def test_domain_validation_and_parser_errors_are_json(self):
        self.store.add_item("sku", 1)
        for arguments in (
            ("reserve", "--key", "key", "--sku", "sku", "--quantity", "2"),
            ("reserve", "--key", "key", "--sku", "missing", "--quantity", "1"),
            ("reserve", "--key", " ", "--sku", "sku", "--quantity", "1"),
            ("release", "--id", "999"), ("release", "--id", "0"),
            ("add", "--sku", "sku", "--quantity", "0"),
            ("add", "--sku", "sku", "--quantity", "1.5"),
            ("add", "--sku", " ", "--quantity", "1"),
            ("add", "--sku", "sku"), ("report", "--unknown"), ("unknown",), (),
            ("serve", "--port", "65536"),
        ):
            with self.subTest(arguments=arguments):
                self.cli(*arguments, expected_status=2)
        self.assertEqual(self.store.report(), {
            "items": [{"sku": "sku", "available": 1}],
            "active_reservations": 0, "reserved_units": 0,
        })
        self.cli("reserve", "--key", "key", "--sku", "sku", "--quantity", "1")
        self.cli("reserve", "--key", "key", "--sku", "sku", "--quantity", "2", expected_status=2)

    def test_cli_serve_and_http_share_database(self):
        with closing(socket.socket()) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        process = subprocess.Popen(
            [sys.executable, "-m", "reservation", "--db", str(self.path),
             "serve", "--host", "127.0.0.1", "--port", str(port)],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 5
            while True:
                try:
                    with closing(http.client.HTTPConnection("127.0.0.1", port, timeout=1)) as connection:
                        connection.request("GET", "/health")
                        response = connection.getresponse()
                        self.assertEqual(response.status, 200)
                        self.assertEqual(json.loads(response.read()), {"ok": True})
                    break
                except OSError:
                    if process.poll() is not None or time.monotonic() >= deadline:
                        self.fail("CLI server failed to start")
                    threading.Event().wait(0.02)
            self.assertIsNone(process.poll())
            self.cli("add", "--sku", "sku", "--quantity", "5")
            with closing(http.client.HTTPConnection("127.0.0.1", port, timeout=5)) as connection:
                connection.request("POST", "/reservations", body=json.dumps({
                    "idempotency_key": "http-key", "sku": "sku", "quantity": 2,
                }), headers={"Content-Type": "application/json"})
                response = connection.getresponse()
                self.assertEqual(response.status, 201)
                record = json.loads(response.read())
            self.assertEqual(self.cli("report")["reserved_units"], 2)
            self.cli("release", "--id", str(record["reservation_id"]))
            self.assertEqual(self.store.get_item("sku")["available"], 5)
        finally:
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
