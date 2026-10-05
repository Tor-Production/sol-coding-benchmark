import http.client
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote

from reservation import Store
from reservation.http_api import create_server


class HttpTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "inventory.db"
        self.server = create_server(self.path)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, method, path, record=None, *, raw=None, headers=None):
        if record is not None:
            raw = json.dumps(record, ensure_ascii=False).encode("utf-8")
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=10)
        try:
            connection.request(method, path, body=raw, headers=headers or {})
            response = connection.getresponse()
            data = response.read()
            self.assertEqual(response.getheader("Content-Type"), "application/json")
            self.assertEqual(int(response.getheader("Content-Length")), len(data))
            payload = json.loads(data.decode("utf-8"))
            if response.status >= 400:
                self.assertEqual(set(payload), {"error"})
                self.assertIsInstance(payload["error"], str)
            return response.status, payload
        finally:
            connection.close()

    def test_full_flow_with_encoded_unicode_sku(self):
        sku = "café/部品 ?#%"
        self.assertEqual(self.request("GET", "/health"), (200, {"ok": True}))
        self.assertEqual(self.request("POST", "/items", {"sku": f" {sku} ", "quantity": 3}),
                         (201, {"sku": sku, "available": 3}))
        self.request("POST", "/items", {"sku": sku, "quantity": 2})
        self.assertEqual(self.request("GET", "/items/" + quote(sku, safe="")),
                         (200, {"sku": sku, "available": 5}))
        body = {"idempotency_key": " key ", "sku": sku, "quantity": 2}
        status, original = self.request("POST", "/reservations", body)
        self.assertEqual(status, 201)
        self.assertEqual(original["idempotency_key"], "key")
        self.assertEqual(self.request("POST", "/reservations", body), (201, original))
        self.assertEqual(self.request("POST", "/reservations", {**body, "quantity": 1})[0], 409)
        self.assertEqual(self.request("GET", "/report")[1], {
            "items": [{"sku": sku, "available": 3}],
            "active_reservations": 1, "reserved_units": 2,
        })
        release_path = f"/reservations/{original['reservation_id']}/release"
        released = {**original, "status": "released"}
        self.assertEqual(self.request("POST", release_path, {}), (200, released))
        self.assertEqual(self.request("POST", release_path, {}), (200, released))
        self.assertEqual(self.request("POST", "/reservations", body), (201, released))
        self.assertEqual(Store(self.path).get_item(sku)["available"], 5)

    def test_invalid_json_and_values_do_not_change_state(self):
        cases = [
            ("/items", b"{"), ("/items", b"[]"), ("/items", b"null"),
            ("/items", b"true"), ("/items", b"1"), ("/items", b'"text"'),
            ("/items", b"\xff"), ("/items", b"{}"),
            ("/items", b"[" * 2000 + b"0" + b"]" * 2000),
            ("/items", b'{"sku":"sku","quantity":true}'),
            ("/items", b'{"sku":"sku","quantity":0}'),
            ("/items", b'{"sku":"sku","quantity":1.0}'),
            ("/items", b'{"sku":"sku","quantity":"1"}'),
            ("/items", b'{"sku":" ","quantity":1}'),
            ("/items", b'{"sku":"sku","quantity":1,"extra":NaN}'),
            ("/reservations", b'{"sku":"sku","quantity":1}'),
            ("/reservations", b'{"idempotency_key":false,"sku":"sku","quantity":1}'),
            ("/reservations/1/release", b"[]"),
        ]
        for path, raw in cases:
            with self.subTest(path=path, raw=raw):
                self.assertEqual(self.request("POST", path, raw=raw)[0], 400)
                self.assertEqual(self.request("GET", "/health")[0], 200)
        self.assertEqual(Store(self.path).report(), {
            "items": [], "active_reservations": 0, "reserved_units": 0
        })

    def test_missing_routes_records_and_invalid_identifiers(self):
        for method, path, body in (
            ("GET", "/unknown", None), ("POST", "/unknown", {}),
            ("PUT", "/health", {}), ("GET", "/items/missing", None),
            ("TRACE", "/health", None),
            ("POST", "/reservations", {"idempotency_key": "key", "sku": "missing", "quantity": 1}),
            ("POST", "/reservations/999/release", {}),
        ):
            with self.subTest(method=method, path=path):
                self.assertEqual(self.request(method, path, body)[0], 404)
        for identifier in ("0", "-1", "true", "1.0", "1_0"):
            with self.subTest(identifier=identifier):
                self.assertEqual(self.request("POST", f"/reservations/{identifier}/release", {})[0], 400)
        self.assertEqual(self.request("GET", "/items/%FF")[0], 400)
        self.assertEqual(self.request("GET", "/health")[0], 200)

    def test_conflict_preserves_stock_and_failed_key_can_be_retried(self):
        self.request("POST", "/items", {"sku": "sku", "quantity": 1})
        body = {"idempotency_key": "key", "sku": "sku", "quantity": 2}
        self.assertEqual(self.request("POST", "/reservations", body)[0], 409)
        self.assertEqual(Store(self.path).get_item("sku")["available"], 1)
        self.request("POST", "/items", {"sku": "sku", "quantity": 1})
        self.assertEqual(self.request("POST", "/reservations", body)[0], 201)

    def test_body_limit_and_invalid_content_length(self):
        base = b'{"sku":"sku","quantity":1}'
        exact_limit = base + b" " * (64 * 1024 - len(base))
        self.assertEqual(self.request("POST", "/items", raw=exact_limit)[0], 201)
        for method, path in (("POST", "/items"), ("GET", "/health")):
            self.assertEqual(self.request(method, path, headers={"Content-Length": "65537"})[0], 413)
        self.assertEqual(self.request("POST", "/items", headers={"Content-Length": "9" * 100})[0], 413)
        for length in ("-1", "abc"):
            self.assertEqual(self.request("POST", "/items", headers={"Content-Length": length})[0], 400)
        self.assertEqual(self.request("POST", "/items")[0], 400)
        self.assertEqual(self.request("POST", "/items", headers={"Transfer-Encoding": "chunked"})[0], 400)
        self.assertEqual(self.request("GET", "/health"), (200, {"ok": True}))
        self.assertEqual(Store(self.path).get_item("sku")["available"], 1)

    def concurrent_requests(self, operation, count=8):
        barrier = threading.Barrier(count)

        def run(index):
            barrier.wait(timeout=15)
            return operation(index)

        with ThreadPoolExecutor(max_workers=count) as executor:
            return list(executor.map(run, range(count)))

    def test_concurrent_requests_cannot_oversell(self):
        Store(self.path).add_item("sku", 3)
        responses = self.concurrent_requests(lambda index: self.request("POST", "/reservations", {
            "idempotency_key": f"key-{index}", "sku": "sku", "quantity": 1
        }))
        self.assertEqual([status for status, _ in responses].count(201), 3)
        self.assertEqual([status for status, _ in responses].count(409), 5)
        self.assertEqual(self.request("GET", "/report")[1], {
            "items": [{"sku": "sku", "available": 0}],
            "active_reservations": 3, "reserved_units": 3,
        })

    def test_concurrent_http_retries_and_releases(self):
        Store(self.path).add_item("sku", 1)
        body = {"idempotency_key": "same", "sku": "sku", "quantity": 1}
        responses = self.concurrent_requests(lambda index: self.request("POST", "/reservations", body))
        self.assertTrue(all(response == responses[0] for response in responses))
        self.assertEqual(responses[0][0], 201)
        record = responses[0][1]
        path = f"/reservations/{record['reservation_id']}/release"
        responses = self.concurrent_requests(lambda index: self.request("POST", path, {}))
        self.assertTrue(all(response == (200, {**record, "status": "released"}) for response in responses))
        self.assertEqual(Store(self.path).report(), {
            "items": [{"sku": "sku", "available": 1}],
            "active_reservations": 0, "reserved_units": 0,
        })
