"""HTTP interface for the inventory reservation store."""

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from .store import Conflict, NotFound, Store


_MAX_BODY = 64 * 1024
_RELEASE_PATH = re.compile(r"^/reservations/([^/]+)/release$")
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


class _PayloadTooLarge(Exception):
    pass


class _RouteNotFound(Exception):
    pass


def _reject_json_constant(value):
    raise ValueError(f"invalid JSON constant: {value}")


def _decode_path_component(component):
    index = 0
    while index < len(component):
        if component[index] == "%":
            if (
                index + 2 >= len(component)
                or component[index + 1] not in _HEX_DIGITS
                or component[index + 2] not in _HEX_DIGITS
            ):
                raise ValueError("invalid URL encoding")
            index += 3
        else:
            index += 1
    return unquote(component, encoding="utf-8", errors="strict")


def create_server(db_path, host="127.0.0.1", port=0):
    """Create and bind a threaded HTTP server without starting its loop."""

    store = Store(db_path)

    class ReservationHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format, *args):
            # A library-created server should not write unsolicited access logs.
            return

        def _json_object(self):
            if self.headers.get("Transfer-Encoding") is not None:
                self.close_connection = True
                raise ValueError("transfer encoding is not supported")

            length_header = self.headers.get("Content-Length")
            if length_header is None:
                raise ValueError("Content-Length is required")
            try:
                length = int(length_header)
            except (TypeError, ValueError) as exc:
                raise ValueError("invalid Content-Length") from exc
            if length < 0:
                raise ValueError("invalid Content-Length")
            if length > _MAX_BODY:
                self.close_connection = True
                raise _PayloadTooLarge("request body exceeds 64 KiB")

            raw = self.rfile.read(length)
            if len(raw) != length:
                self.close_connection = True
                raise ValueError("incomplete request body")
            try:
                value = json.loads(
                    raw.decode("utf-8"), parse_constant=_reject_json_constant
                )
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("malformed JSON") from exc
            if not isinstance(value, dict):
                raise ValueError("JSON body must be an object")
            return value

        @staticmethod
        def _field(body, name):
            if name not in body:
                raise ValueError(f"missing field: {name}")
            return body[name]

        def _dispatch(self, method):
            try:
                path = urlsplit(self.path).path
            except ValueError as exc:
                raise ValueError("invalid request path") from exc

            if method == "GET" and path == "/health":
                return 200, {"ok": True}

            if method == "GET" and path == "/report":
                return 200, store.report()

            if method == "GET" and path.startswith("/items/"):
                encoded_sku = path[len("/items/") :]
                if "/" in encoded_sku:
                    raise _RouteNotFound("route not found")
                return 200, store.get_item(_decode_path_component(encoded_sku))

            if method == "POST" and path == "/items":
                body = self._json_object()
                record = store.add_item(
                    self._field(body, "sku"), self._field(body, "quantity")
                )
                return 201, record

            if method == "POST" and path == "/reservations":
                body = self._json_object()
                record = store.reserve(
                    self._field(body, "idempotency_key"),
                    self._field(body, "sku"),
                    self._field(body, "quantity"),
                )
                return 201, record

            if method == "POST":
                match = _RELEASE_PATH.fullmatch(path)
                if match is not None:
                    body = self._json_object()
                    del body  # The route requires a JSON object but no fields.
                    id_text = _decode_path_component(match.group(1))
                    if not id_text.isascii() or not id_text.isdecimal():
                        raise ValueError(
                            "reservation_id must be a positive integer"
                        )
                    reservation_id = int(id_text)
                    return 200, store.release(reservation_id)

            if method != "GET":
                # Do not leave an unconsumed body on a persistent connection.
                self.close_connection = True
            raise _RouteNotFound("route not found")

        def _send_json(self, status, value):
            encoded = json.dumps(
                value, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            if self.close_connection:
                self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(encoded)

        def _respond(self, method):
            try:
                status, result = self._dispatch(method)
            except _PayloadTooLarge as exc:
                status, result = 413, {"error": str(exc)}
            except _RouteNotFound as exc:
                status, result = 404, {"error": str(exc)}
            except NotFound as exc:
                status, result = 404, {"error": str(exc)}
            except Conflict as exc:
                status, result = 409, {"error": str(exc)}
            except (ValueError, KeyError) as exc:
                status, result = 400, {"error": str(exc)}
            except Exception:
                status, result = 500, {"error": "internal server error"}

            try:
                self._send_json(status, result)
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True

        def do_GET(self):
            self._respond("GET")

        def do_POST(self):
            self._respond("POST")

        def do_HEAD(self):
            self._respond("HEAD")

        def do_PUT(self):
            self._respond("PUT")

        def do_PATCH(self):
            self._respond("PATCH")

        def do_DELETE(self):
            self._respond("DELETE")

        def do_OPTIONS(self):
            self._respond("OPTIONS")

    return ThreadingHTTPServer((host, port), ReservationHandler)
