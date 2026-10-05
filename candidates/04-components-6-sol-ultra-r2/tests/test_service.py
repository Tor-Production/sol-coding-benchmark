import http.client
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

from reservation.http_api import create_server
from reservation.store import Conflict, NotFound, Store


class ServiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = str(Path(temporary.name) / "stock.db")

    def test_reopen_retry_release_and_large_integer(self):
        store = Store(self.db)
        huge = 2**63 + 5
        self.assertEqual(store.add_item("  b  ", huge)["available"], huge)
        store.add_item("a", 2)

        reopened = Store(self.db)
        first = reopened.reserve("  request  ", " b ", huge)
        self.assertEqual(store.reserve("request", "b", huge), first)
        self.assertEqual(store.get_item("b")["available"], 0)
        self.assertEqual(store.report()["reserved_units"], huge)
        with self.assertRaises(Conflict):
            store.reserve("request", "a", 1)

        released = store.release(first["reservation_id"])
        self.assertEqual(released["status"], "released")
        self.assertEqual(reopened.release(first["reservation_id"]), released)
        self.assertEqual(reopened.reserve("request", "b", huge), first)
        self.assertEqual(
            Store(self.db).report(),
            {
                "items": [
                    {"sku": "a", "available": 2},
                    {"sku": "b", "available": huge},
                ],
                "active_reservations": 0,
                "reserved_units": 0,
            },
        )
        with self.assertRaises(ValueError):
            store.add_item("a", True)
        with self.assertRaises(NotFound):
            store.release(2**80)

    def test_concurrent_stores_do_not_oversell_or_duplicate(self):
        Store(self.db).add_item("sku", 20)

        def reserve_one(number):
            try:
                return Store(self.db).reserve(f"key-{number}", "sku", 1)
            except Conflict:
                return None

        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(reserve_one, range(40)))
        successes = [result for result in results if result is not None]
        self.assertEqual(len(successes), 20)
        self.assertEqual(len({record["reservation_id"] for record in successes}), 20)
        self.assertEqual(Store(self.db).get_item("sku")["available"], 0)

        Store(self.db).add_item("sku", 1)
        with ThreadPoolExecutor(max_workers=10) as pool:
            duplicates = list(
                pool.map(lambda _: Store(self.db).reserve("same", "sku", 1), range(10))
            )
        self.assertEqual(len({record["reservation_id"] for record in duplicates}), 1)
        self.assertEqual(Store(self.db).report()["active_reservations"], 21)
        self.assertEqual(Store(self.db).get_item("sku")["available"], 0)

    def test_http_json_status_and_body_limit(self):
        server = create_server(self.db)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop_server():
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.addCleanup(stop_server)

        def request(method, path, body=None):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            headers = {}
            if body is not None and not isinstance(body, (bytes, bytearray)):
                body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            if body is not None:
                headers["Content-Type"] = "application/json"
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read()
            result = response.status, response.getheader("Content-Type"), response.getheader("Content-Length"), json.loads(raw)
            self.assertEqual(int(result[2]), len(raw))
            connection.close()
            return result

        self.assertEqual(request("GET", "/health")[3], {"ok": True})
        sku = "café"
        status, content_type, _, item = request("POST", "/items", {"sku": sku, "quantity": 3})
        self.assertEqual((status, content_type), (201, "application/json"))
        self.assertEqual(item, {"sku": sku, "available": 3})
        self.assertEqual(request("GET", "/items/" + quote(sku))[3], item)

        reservation_body = {"idempotency_key": "key", "sku": sku, "quantity": 2}
        reservation = request("POST", "/reservations", reservation_body)[3]
        self.assertEqual(request("POST", "/reservations", reservation_body)[3], reservation)
        self.assertEqual(request("POST", "/reservations", {**reservation_body, "quantity": 1})[0], 409)
        released = request("POST", f"/reservations/{reservation['reservation_id']}/release", {})
        self.assertEqual((released[0], released[3]["status"]), (200, "released"))
        self.assertEqual(request("POST", "/reservations", reservation_body)[3], reservation)
        self.assertEqual(request("GET", "/report")[3]["reserved_units"], 0)

        self.assertEqual(request("POST", "/items", b"{")[0], 400)
        self.assertEqual(request("POST", "/items", b'{"sku":"bad","quantity":NaN}')[0], 400)
        self.assertEqual(request("POST", "/items", [])[0], 400)
        self.assertEqual(request("POST", "/items", {"sku": "missing"})[0], 400)
        self.assertEqual(request("POST", "/items", {"sku": "bad", "quantity": True})[0], 400)
        self.assertEqual(request("POST", "/items", b" " * (65536 + 1))[0], 413)
        self.assertEqual(request("GET", "/missing")[0], 404)

        request("POST", "/items", {"sku": "concurrent", "quantity": 5})
        with ThreadPoolExecutor(max_workers=10) as pool:
            statuses = list(
                pool.map(
                    lambda index: request(
                        "POST",
                        "/reservations",
                        {"idempotency_key": f"http-{index}", "sku": "concurrent", "quantity": 1},
                    )[0],
                    range(10),
                )
            )
        self.assertEqual(statuses.count(201), 5)
        self.assertEqual(statuses.count(409), 5)
        self.assertEqual(request("GET", "/items/concurrent")[3]["available"], 0)
        self.assertEqual(request("GET", "/health")[0], 200)

    def test_cli_uses_same_database_and_json_errors(self):
        root = Path(__file__).resolve().parent.parent

        def cli(*arguments):
            return subprocess.run(
                [sys.executable, "-m", "reservation", "--db", self.db, *arguments],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=10,
            )

        added = cli("add", "--sku", "sku", "--quantity", "4")
        self.assertEqual(added.returncode, 0)
        self.assertEqual(json.loads(added.stdout), {"sku": "sku", "available": 4})
        self.assertEqual(Store(self.db).get_item("sku")["available"], 4)

        reserved = cli("reserve", "--key", "key", "--sku", "sku", "--quantity", "2")
        self.assertEqual(reserved.returncode, 0)
        reservation_id = json.loads(reserved.stdout)["reservation_id"]
        self.assertEqual(Store(self.db).report()["reserved_units"], 2)

        released = cli("release", "--id", str(reservation_id))
        self.assertEqual(json.loads(released.stdout)["status"], "released")
        invalid = cli("add", "--sku", "sku", "--quantity", "0")
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(invalid.stdout, "")
        self.assertIn("error", json.loads(invalid.stderr))
        invalid_type = cli("release", "--id", "invalid")
        self.assertEqual(invalid_type.returncode, 2)
        self.assertIn("error", json.loads(invalid_type.stderr))
