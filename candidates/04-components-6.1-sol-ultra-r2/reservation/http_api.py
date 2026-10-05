"""A small JSON HTTP API using the standard-library threaded server."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from .store import Conflict, NotFound, Store


MAX_BODY_SIZE = 64 * 1024


class _RequestError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


def _reject_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


def create_server(db_path, host="127.0.0.1", port=0):
    """Bind a server; the caller owns serve_forever/shutdown/server_close."""
    store = Store(db_path)

    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, status, record):
            body = json.dumps(record, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def send_error(self, code, message=None, explain=None):
            # Keep parser errors and unsupported methods in the JSON format.
            if code == 501:
                code, message = 404, "Route not found"
            self.close_connection = True
            self._send_json(code, {"error": message or self.responses.get(code, ("HTTP error",))[0]})

        def log_message(self, format, *args):
            pass

        def _body_length(self):
            if self.headers.get("Transfer-Encoding") is not None:
                raise _RequestError(400, "Transfer-Encoding is not supported")
            headers = self.headers.get_all("Content-Length", [])
            if not headers:
                return 0
            if len(headers) != 1:
                raise _RequestError(400, "Invalid Content-Length")
            value = headers[0].strip()
            if not value.isascii() or not value.isdecimal():
                raise _RequestError(400, "Invalid Content-Length")
            # Ignore leading zeroes before checking size, avoiding huge int
            # conversions on a header containing thousands of digits.
            significant = value.lstrip("0") or "0"
            if len(significant) > 5 or int(significant) > MAX_BODY_SIZE:
                raise _RequestError(413, "Request body exceeds 64 KiB")
            return int(significant)

        def _read_object(self, length):
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("Incomplete request body")
            try:
                record = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
            except (ValueError, RecursionError) as error:
                raise ValueError("Malformed JSON") from error
            if not isinstance(record, dict):
                raise ValueError("JSON body must be an object")
            return record

        def _require(self, record, *fields):
            for field in fields:
                if field not in record:
                    raise ValueError(f"Missing field: {field}")

        def _route(self):
            length = self._body_length()
            path = urlsplit(self.path).path
            if self.command == "GET":
                if path == "/health":
                    return 200, {"ok": True}
                if path == "/report":
                    return 200, store.report()
                if path.startswith("/items/"):
                    sku = path[len("/items/"):]
                    if sku and "/" not in sku:
                        return 200, store.get_item(unquote(sku, encoding="utf-8", errors="strict"))
            elif self.command == "POST":
                if path == "/items":
                    record = self._read_object(length)
                    self._require(record, "sku", "quantity")
                    return 201, store.add_item(record["sku"], record["quantity"])
                if path == "/reservations":
                    record = self._read_object(length)
                    self._require(record, "idempotency_key", "sku", "quantity")
                    return 201, store.reserve(record["idempotency_key"], record["sku"], record["quantity"])
                if path.startswith("/reservations/") and path.endswith("/release"):
                    reservation_id = path[len("/reservations/"):-len("/release")]
                    if "/" not in reservation_id:
                        self._read_object(length)
                        try:
                            reservation_id = int(reservation_id)
                        except ValueError as error:
                            raise ValueError("reservation_id must be a positive integer") from error
                        return 200, store.release(reservation_id)
            raise _RequestError(404, "Route not found")

        def _handle(self):
            # Responses close the connection, so rejected or oversized bodies
            # never become another request on a persistent connection.
            self.close_connection = True
            try:
                status, record = self._route()
            except _RequestError as error:
                status, record = error.status, {"error": str(error)}
            except NotFound as error:
                status, record = 404, {"error": str(error)}
            except Conflict as error:
                status, record = 409, {"error": str(error)}
            except ValueError as error:
                status, record = 400, {"error": str(error)}
            except Exception:
                status, record = 500, {"error": "Internal server error"}
            self._send_json(status, record)

        do_GET = _handle
        do_POST = _handle
        do_PUT = _handle
        do_PATCH = _handle
        do_DELETE = _handle
        do_HEAD = _handle
        do_OPTIONS = _handle

    return ThreadingHTTPServer((host, port), Handler)
