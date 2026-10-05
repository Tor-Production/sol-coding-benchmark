"""JSON HTTP interface to the inventory store."""

import json
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from .store import Conflict, NotFound, Store


MAX_BODY_BYTES = 64 * 1024


class _BodyTooLarge(Exception):
    pass


def _reject_json_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


def create_server(db_path, host="127.0.0.1", port=0):
    """Bind a threaded HTTP server; the caller controls its serving lifecycle."""
    store = Store(db_path)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def _send_json(self, status, record):
            body = json.dumps(record, ensure_ascii=False).encode("utf-8")
            self.close_connection = True
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def send_error(self, code, message=None, explain=None):
            if code == 501:
                code, message = 404, "Route not found"
            self._send_json(code, {"error": message or self.responses[code][0]})

        def _body_length(self):
            if self.headers.get("Transfer-Encoding") is not None:
                raise ValueError("Transfer-Encoding is not supported")
            lengths = self.headers.get_all("Content-Length", [])
            if not lengths:
                return 0
            if len(lengths) != 1 or not re.fullmatch(r"[0-9]+", lengths[0]):
                raise ValueError("Invalid Content-Length")
            # Compare decimal strings first, even for arbitrarily large headers.
            length = lengths[0].lstrip("0") or "0"
            maximum = str(MAX_BODY_BYTES)
            if len(length) > len(maximum) or (
                len(length) == len(maximum) and length > maximum
            ):
                raise _BodyTooLarge("Request body exceeds 64 KiB")
            return int(length)

        def _read_object(self, length):
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("Incomplete request body")
            try:
                record = json.loads(
                    raw.decode("utf-8"), parse_constant=_reject_json_constant
                )
            except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
                raise ValueError("Malformed JSON") from error
            if not isinstance(record, dict):
                raise ValueError("Request JSON must be an object")
            return record

        def _dispatch(self, length):
            path = urlsplit(self.path).path
            if self.command == "GET":
                if path == "/health":
                    return 200, {"ok": True}
                if path == "/report":
                    return 200, store.report()
                if path.startswith("/items/"):
                    sku = unquote(path[len("/items/"):], errors="strict")
                    return 200, store.get_item(sku)
            elif self.command == "POST":
                if path == "/items":
                    body = self._read_object(length)
                    return 201, store.add_item(body["sku"], body["quantity"])
                if path == "/reservations":
                    body = self._read_object(length)
                    return 201, store.reserve(
                        body["idempotency_key"], body["sku"], body["quantity"]
                    )
                match = re.fullmatch(r"/reservations/([^/]+)/release", path)
                if match:
                    self._read_object(length)
                    identifier = match.group(1)
                    if not re.fullmatch(r"[0-9]+", identifier):
                        raise ValueError("reservation_id must be a positive integer")
                    return 200, store.release(int(identifier))
            return 404, {"error": "Route not found"}

        def _handle(self):
            try:
                status, record = self._dispatch(self._body_length())
            except _BodyTooLarge as error:
                status, record = 413, {"error": str(error)}
            except NotFound as error:
                status, record = 404, {"error": str(error)}
            except Conflict as error:
                status, record = 409, {"error": str(error)}
            except KeyError as error:
                status, record = 400, {"error": f"Missing field: {error.args[0]}"}
            except ValueError as error:
                status, record = 400, {"error": str(error)}
            except sqlite3.Error:
                status, record = 500, {"error": "Database error"}
            try:
                self._send_json(status, record)
            except (BrokenPipeError, ConnectionResetError):
                pass

        do_GET = _handle
        do_POST = _handle
        do_HEAD = _handle
        do_PUT = _handle
        do_PATCH = _handle
        do_DELETE = _handle
        do_OPTIONS = _handle

    return ThreadingHTTPServer((host, port), Handler)
