import http.client
import json
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from reservation import Store
from reservation.__main__ import main
from reservation.http_api import create_server


class CliTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "inventory.db"

    def run_cli(self, *arguments, expected_status=0):
        result = subprocess.run(
            [sys.executable, "-m", "reservation", "--db", str(self.path), *arguments],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, expected_status, result.stderr)
        if expected_status == 0:
            self.assertEqual(result.stderr, "")
            self.assertEqual(len(result.stdout.splitlines()), 1)
            return json.loads(result.stdout)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(result.stderr.splitlines()), 1)
        error = json.loads(result.stderr)
        self.assertEqual(set(error), {"error"})
        self.assertIsInstance(error["error"], str)
        return error

    def test_finite_commands_persist_and_share_state(self):
        self.assertEqual(self.run_cli("add", "--sku", " sku ", "--quantity", "5"),
                         {"sku": "sku", "available": 5})
        arguments = ("reserve", "--key", "key", "--sku", "sku", "--quantity", "2")
        original = self.run_cli(*arguments)
        self.assertEqual(self.run_cli(*arguments), original)
        self.assertEqual(Store(self.path).get_item("sku")["available"], 3)
        released = self.run_cli("release", "--id", str(original["reservation_id"]))
        self.assertEqual(released, {**original, "status": "released"})
        self.assertEqual(self.run_cli(*arguments), released)
        self.assertEqual(self.run_cli("report"), {
            "items": [{"sku": "sku", "available": 5}],
            "active_reservations": 0, "reserved_units": 0,
        })

    def test_domain_and_parser_failures_are_json_on_stderr(self):
        self.run_cli("add", "--sku", "sku", "--quantity", "1")
        for arguments in (
            (), ("unknown",), ("add", "--sku", "sku"),
            ("add", "--sku", "sku", "--quantity", "abc"),
            ("add", "--sku", "sku", "--quantity", "0"),
            ("add", "--sku", " ", "--quantity", "1"),
            ("reserve", "--key", "key", "--sku", "sku", "--quantity", "2"),
            ("reserve", "--key", "key", "--sku", "absent", "--quantity", "1"),
            ("release", "--id", "0"), ("release", "--id", "999"),
            ("serve", "--port", "-1"),
        ):
            with self.subTest(arguments=arguments):
                self.run_cli(*arguments, expected_status=2)
        self.assertEqual(Store(self.path).get_item("sku")["available"], 1)

    def test_http_cli_and_store_use_the_same_database(self):
        self.run_cli("add", "--sku", "sku", "--quantity", "4")
        server = create_server(self.path)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection(*server.server_address, timeout=10)
            try:
                body = json.dumps({"idempotency_key": "key", "sku": "sku", "quantity": 3})
                connection.request("POST", "/reservations", body=body)
                response = connection.getresponse()
                self.assertEqual(response.status, 201)
                record = json.loads(response.read())
            finally:
                connection.close()
            self.assertEqual(self.run_cli("report")["reserved_units"], 3)
            self.run_cli("release", "--id", str(record["reservation_id"]))
            self.assertEqual(Store(self.path).get_item("sku")["available"], 4)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_serve_closes_server_when_interrupted(self):
        with patch("reservation.__main__.create_server") as create:
            server = create.return_value
            server.serve_forever.side_effect = KeyboardInterrupt
            self.assertEqual(main(["--db", str(self.path), "serve", "--port", "8123"]), 0)
            create.assert_called_once_with(str(self.path), host="127.0.0.1", port=8123)
            server.server_close.assert_called_once()
