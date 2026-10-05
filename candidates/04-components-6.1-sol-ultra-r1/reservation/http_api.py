"""A bounded JSON HTTP interface to the reservation store."""

import json
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from .store import Conflict, NotFound, Store


MAX_BODY_BYTES = 64 * 1024


class _RequestError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _reject_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


def create_server(db_path, host="127.0.0.1", port=0):
    """Initialize the database and return a bound, threaded HTTP server."""
    store = Store(db_path)

    class Handler(BaseHTTPRequestHandler):
        # Closing each response also safely discards unread invalid/oversize bodies.
        protocol_version = "HTTP/1.0"
        timeout = 10

        def log_message(self, format, *args):
            # API errors are returned as JSON; do not write request logs to stderr.
            pass

        def _send_json(self, status, record):
            payload = json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            if self.command != "HEAD":
                self.wfile.write(payload)

        def send_error(self, code, message=None, explain=None):
            if code == HTTPStatus.NOT_IMPLEMENTED:
                code = HTTPStatus.NOT_FOUND
                message = "Route not found"
            self._send_json(code, {"error": message or HTTPStatus(code).phrase})

        def _body_length(self):
            if self.headers.get("Transfer-Encoding") is not None:
                raise _RequestError(400, "Transfer-Encoding is not supported")
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) > 1:
                raise _RequestError(400, "Multiple Content-Length headers")
            if not lengths:
                return 0
            value = lengths[0].strip()
            if not value or any(char < "0" or char > "9" for char in value):
                raise _RequestError(400, "Invalid Content-Length")
            value = value.lstrip("0") or "0"
            # Compare before converting, including headers with very many digits.
            limit = str(MAX_BODY_BYTES)
            if len(value) > len(limit) or (len(value) == len(limit) and value > limit):
                raise _RequestError(413, "Request body exceeds 64 KiB")
            return int(value)

        def _read_object(self, length):
            try:
                data = self.rfile.read(length)
                if len(data) != length:
                    raise ValueError("Incomplete body")
                body = json.loads(data.decode("utf-8"), parse_constant=_reject_constant)
            except (ValueError, RecursionError, OSError) as error:
                raise _RequestError(400, "Malformed JSON request body") from error
            if not isinstance(body, dict):
                raise _RequestError(400, "JSON request body must be an object")
            return body

        def _dispatch(self):
            try:
                length = self._body_length()
                path = urlsplit(self.path).path
                if self.command == "GET":
                    if path == "/health":
                        result = {"ok": True}
                    elif path == "/report":
                        result = store.report()
                    elif path.startswith("/items/") and path[7:] and "/" not in path[7:]:
                        result = store.get_item(unquote(path[7:], encoding="utf-8", errors="strict"))
                    else:
                        raise NotFound("Route not found")
                    self._send_json(200, result)
                    return

                release_match = re.fullmatch(r"/reservations/([^/]+)/release", path)
                if path not in ("/items", "/reservations") and release_match is None:
                    raise NotFound("Route not found")
                body = self._read_object(length)
                if path == "/items":
                    result = store.add_item(body["sku"], body["quantity"])
                    status = 201
                elif path == "/reservations":
                    result = store.reserve(body["idempotency_key"], body["sku"], body["quantity"])
                    status = 201
                else:
                    raw_id = release_match.group(1)
                    if not raw_id.isascii() or not raw_id.isdecimal():
                        raise ValueError("reservation_id must be a positive integer")
                    result = store.release(int(raw_id))
                    status = 200
                self._send_json(status, result)
            except _RequestError as error:
                self._send_json(error.status, {"error": str(error)})
            except NotFound as error:
                self._send_json(404, {"error": str(error)})
            except Conflict as error:
                self._send_json(409, {"error": str(error)})
            except KeyError as error:
                self._send_json(400, {"error": f"Missing field: {error.args[0]}"})
            except ValueError as error:
                self._send_json(400, {"error": str(error)})

        do_GET = _dispatch
        do_POST = _dispatch

    return ThreadingHTTPServer((host, port), Handler)
