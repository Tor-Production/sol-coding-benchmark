"""Standard-library JSON HTTP API for the reservation store."""

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from .store import Conflict, NotFound, Store


_MAX_BODY_BYTES = 64 * 1024
_RELEASE_PATH = re.compile(r"^/reservations/([^/]+)/release$")


def create_server(db_path, host="127.0.0.1", port=0):
    """Create and bind a threaded HTTP server without starting its loop."""

    store = Store(db_path)

    class ReservationHandler(BaseHTTPRequestHandler):
        server_version = "ReservationHTTP/1.0"

        def log_message(self, format, *args):
            # A service embedding this server should decide how to log.  In
            # particular, CLI JSON output must not be mixed with access logs.
            return

        def _send_json(self, status, value):
            encoded = json.dumps(
                value, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _error(self, status, error):
            message = str(error) or type(error).__name__
            self._send_json(status, {"error": message})

        def _read_object(self):
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                raise ValueError("Content-Length is required")
            try:
                length = int(raw_length, 10)
            except (TypeError, ValueError):
                raise ValueError("invalid Content-Length") from None
            if length < 0:
                raise ValueError("invalid Content-Length")
            if length > _MAX_BODY_BYTES:
                self.close_connection = True
                raise _BodyTooLarge("request body exceeds 64 KiB")
            raw = self.rfile.read(length)
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ValueError("malformed JSON") from None
            if not isinstance(value, dict):
                raise ValueError("JSON request body must be an object")
            return value

        @staticmethod
        def _required(body, name):
            if name not in body:
                raise ValueError(f"missing field: {name}")
            return body[name]

        def _dispatch(self, method):
            path = urlsplit(self.path).path

            if method == "GET" and path == "/health":
                return 200, {"ok": True}
            if method == "GET" and path == "/report":
                return 200, store.report()
            if method == "GET" and path.startswith("/items/"):
                encoded_sku = path[len("/items/") :]
                if "/" in encoded_sku:
                    raise _UnknownRoute("route not found")
                return 200, store.get_item(unquote(encoded_sku))

            if method == "POST" and path == "/items":
                body = self._read_object()
                record = store.add_item(
                    self._required(body, "sku"),
                    self._required(body, "quantity"),
                )
                return 201, record
            if method == "POST" and path == "/reservations":
                body = self._read_object()
                record = store.reserve(
                    self._required(body, "idempotency_key"),
                    self._required(body, "sku"),
                    self._required(body, "quantity"),
                )
                return 201, record
            if method == "POST":
                match = _RELEASE_PATH.fullmatch(path)
                if match is not None:
                    self._read_object()
                    try:
                        reservation_id = int(unquote(match.group(1)), 10)
                    except ValueError:
                        raise ValueError(
                            "reservation id must be a positive integer"
                        ) from None
                    return 200, store.release(reservation_id)

            raise _UnknownRoute("route not found")

        def _handle(self, method):
            try:
                status, value = self._dispatch(method)
                self._send_json(status, value)
            except _BodyTooLarge as error:
                self._error(413, error)
            except _UnknownRoute as error:
                self._error(404, error)
            except NotFound as error:
                self._error(404, error)
            except Conflict as error:
                self._error(409, error)
            except ValueError as error:
                self._error(400, error)
            except Exception as error:
                # One bad request must not terminate a handler thread or expose
                # a traceback to a client.
                self._error(500, error)

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def do_PUT(self):
            self._handle("PUT")

        def do_PATCH(self):
            self._handle("PATCH")

        def do_DELETE(self):
            self._handle("DELETE")

    return ThreadingHTTPServer((host, port), ReservationHandler)


class _BodyTooLarge(Exception):
    pass


class _UnknownRoute(Exception):
    pass
