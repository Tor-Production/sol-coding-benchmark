"""JSON HTTP interface for the inventory store."""

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from .store import Conflict, NotFound, Store


MAX_BODY_BYTES = 64 * 1024


class _BodyTooLarge(Exception):
    pass


def create_server(db_path, host="127.0.0.1", port=0):
    store = Store(db_path)

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, format, *args):
            # Leave stdout/stderr available for the command's JSON output.
            pass

        def _send_json(self, status, record):
            body = json.dumps(record).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            if getattr(self, "command", None) != "HEAD":
                self.wfile.write(body)

        def send_error(self, code, message=None, explain=None):
            # BaseHTTPRequestHandler also uses this for unsupported methods
            # and malformed HTTP; these errors must have the same JSON shape.
            if code == 501:
                code, message = 404, "Route not found"
            self._send_json(code, {"error": message or self.responses[code][0]})

        def _body_length(self):
            if self.headers.get("Transfer-Encoding") is not None:
                raise ValueError("Transfer-Encoding is not supported")
            lengths = self.headers.get_all("Content-Length", [])
            if not lengths:
                return 0
            if len(lengths) != 1 or not re.fullmatch(r"[0-9]+", lengths[0].strip()):
                raise ValueError("Invalid Content-Length")
            digits = lengths[0].strip().lstrip("0") or "0"
            maximum = str(MAX_BODY_BYTES)
            if len(digits) > len(maximum) or (
                len(digits) == len(maximum) and digits > maximum
            ):
                raise _BodyTooLarge
            return int(digits)

        def _json_object(self, length):
            try:
                body = self.rfile.read(length)
            except OSError as error:
                raise ValueError("Unable to read request body") from error
            if len(body) != length:
                raise ValueError("Incomplete request body")

            def reject_constant(value):
                raise ValueError(f"Invalid JSON constant: {value}")

            try:
                payload = json.loads(body.decode("utf-8"), parse_constant=reject_constant)
            except (ValueError, RecursionError) as error:
                raise ValueError("Malformed JSON") from error
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
            return payload

        @staticmethod
        def _required(payload, field):
            if field not in payload:
                raise ValueError(f"Missing field: {field}")
            return payload[field]

        def _route(self, length):
            path = urlsplit(self.path).path
            if self.command == "GET":
                if path == "/health":
                    return 200, {"ok": True}
                if path == "/report":
                    return 200, store.report()
                if path.startswith("/items/") and "/" not in path[len("/items/"):]:
                    sku = unquote(path[len("/items/"):], encoding="utf-8", errors="strict")
                    return 200, store.get_item(sku)
            elif self.command == "POST":
                if path == "/items":
                    payload = self._json_object(length)
                    return 201, store.add_item(
                        self._required(payload, "sku"),
                        self._required(payload, "quantity"),
                    )
                if path == "/reservations":
                    payload = self._json_object(length)
                    return 201, store.reserve(
                        self._required(payload, "idempotency_key"),
                        self._required(payload, "sku"),
                        self._required(payload, "quantity"),
                    )
                match = re.fullmatch(r"/reservations/([^/]+)/release", path)
                if match:
                    self._json_object(length)
                    if not re.fullmatch(r"[0-9]+", match.group(1)):
                        raise ValueError("reservation_id must be a positive integer")
                    try:
                        reservation_id = int(match.group(1))
                    except ValueError as error:
                        raise ValueError("reservation_id must be a positive integer") from error
                    return 200, store.release(reservation_id)
            raise NotFound("Route not found")

        def _handle(self):
            try:
                status, record = self._route(self._body_length())
            except _BodyTooLarge:
                status, record = 413, {"error": "Request body exceeds 64 KiB"}
            except ValueError as error:
                status, record = 400, {"error": str(error)}
            except NotFound as error:
                status, record = 404, {"error": str(error)}
            except Conflict as error:
                status, record = 409, {"error": str(error)}
            except Exception:
                status, record = 500, {"error": "Internal server error"}
            self._send_json(status, record)

        do_GET = _handle
        do_POST = _handle
        do_PUT = _handle
        do_DELETE = _handle
        do_PATCH = _handle
        do_OPTIONS = _handle
        do_HEAD = _handle

    return ThreadingHTTPServer((host, port), Handler)
