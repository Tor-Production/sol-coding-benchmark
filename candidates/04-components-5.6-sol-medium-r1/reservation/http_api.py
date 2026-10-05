"""Standard-library JSON HTTP API for the reservation store."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from .store import Conflict, NotFound, Store


_MAX_BODY = 64 * 1024


class _BodyTooLarge(Exception):
    pass


def _reject_json_constant(value):
    raise ValueError(f"invalid JSON constant: {value}")


def create_server(db_path, host="127.0.0.1", port=0):
    """Create and bind a threaded HTTP server without starting its loop."""
    store = Store(db_path)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, status, value):
            body = json.dumps(value, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _error(self, status, error):
            self._send(status, {"error": str(error)})

        def _json_body(self):
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                raise ValueError("Content-Length is required")
            try:
                length = int(raw_length)
            except (TypeError, ValueError):
                raise ValueError("invalid Content-Length") from None
            if length < 0:
                raise ValueError("invalid Content-Length")
            if length > _MAX_BODY:
                self.close_connection = True
                raise _BodyTooLarge()
            raw = self.rfile.read(length)
            try:
                value = json.loads(
                    raw.decode("utf-8"),
                    parse_constant=_reject_json_constant,
                )
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ValueError("malformed JSON") from None
            if not isinstance(value, dict):
                raise ValueError("JSON body must be an object")
            return value

        @staticmethod
        def _fields(value, *names):
            try:
                return tuple(value[name] for name in names)
            except KeyError as error:
                raise ValueError(f"missing field: {error.args[0]}") from None

        def _dispatch(self):
            path = urlsplit(self.path).path
            if self.command == "GET" and path == "/health":
                return 200, {"ok": True}
            if self.command == "GET" and path == "/report":
                return 200, store.report()
            if self.command == "GET" and path.startswith("/items/"):
                encoded_sku = path[len("/items/") :]
                if not encoded_sku or "/" in encoded_sku:
                    raise NotFound("route not found")
                return 200, store.get_item(unquote(encoded_sku))
            if self.command == "POST" and path == "/items":
                body = self._json_body()
                sku, quantity = self._fields(body, "sku", "quantity")
                return 201, store.add_item(sku, quantity)
            if self.command == "POST" and path == "/reservations":
                body = self._json_body()
                key, sku, quantity = self._fields(
                    body, "idempotency_key", "sku", "quantity"
                )
                return 201, store.reserve(key, sku, quantity)
            prefix, suffix = "/reservations/", "/release"
            if self.command == "POST" and path.startswith(prefix) and path.endswith(suffix):
                encoded_id = path[len(prefix) : -len(suffix)]
                if not encoded_id or "/" in encoded_id:
                    raise NotFound("route not found")
                self._json_body()
                try:
                    reservation_id = int(encoded_id)
                except ValueError:
                    raise ValueError("reservation id must be a positive integer") from None
                return 200, store.release(reservation_id)
            raise NotFound("route not found")

        def _handle(self):
            try:
                # Apply the body limit before route matching as a property of
                # the HTTP service, including for unknown routes.
                raw_length = self.headers.get("Content-Length")
                if raw_length is not None:
                    try:
                        length = int(raw_length)
                    except ValueError:
                        raise ValueError("invalid Content-Length") from None
                    if length < 0:
                        raise ValueError("invalid Content-Length")
                    if length > _MAX_BODY:
                        self.close_connection = True
                        raise _BodyTooLarge()
                status, value = self._dispatch()
                self._send(status, value)
            except _BodyTooLarge:
                self._error(413, "request body too large")
            except ValueError as error:
                self._error(400, error)
            except NotFound as error:
                # An unknown POST-like route has not consumed its body, so it
                # cannot safely remain on a persistent connection.
                if self.headers.get("Content-Length") not in (None, "0"):
                    self.close_connection = True
                self._error(404, error)
            except Conflict as error:
                self._error(409, error)
            except Exception:
                self._error(500, "internal server error")

        do_GET = _handle
        do_POST = _handle
        do_PUT = _handle
        do_PATCH = _handle
        do_DELETE = _handle
        do_OPTIONS = _handle

        def log_message(self, format, *args):
            pass

    return ThreadingHTTPServer((host, port), Handler)
