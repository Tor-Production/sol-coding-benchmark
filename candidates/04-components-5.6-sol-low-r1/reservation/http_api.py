"""Standard-library JSON HTTP API."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from .store import Conflict, NotFound, Store


MAX_BODY = 64 * 1024


def create_server(db_path, host="127.0.0.1", port=0):
    store = Store(db_path)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def _send(self, status, value):
            data = json.dumps(value, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _error(self, status, error):
            self._send(status, {"error": str(error)})

        def _body(self):
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                raise ValueError("Content-Length is required")
            try:
                length = int(raw_length)
            except ValueError:
                raise ValueError("invalid Content-Length") from None
            if length < 0:
                raise ValueError("invalid Content-Length")
            if length > MAX_BODY:
                self.close_connection = True
                raise OverflowError("request body too large")
            raw = self.rfile.read(length)
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ValueError("malformed JSON") from None
            if not isinstance(value, dict):
                raise ValueError("JSON body must be an object")
            return value

        @staticmethod
        def _fields(body, names):
            missing = [name for name in names if name not in body]
            if missing:
                raise ValueError(f"missing field: {missing[0]}")
            return [body[name] for name in names]

        def _dispatch(self):
            path = urlsplit(self.path).path
            if self.command == "GET" and path == "/health":
                return 200, {"ok": True}
            if self.command == "GET" and path == "/report":
                return 200, store.report()
            if self.command == "GET" and path.startswith("/items/"):
                encoded = path[len("/items/"):]
                if not encoded or "/" in encoded:
                    raise NotFound("route not found")
                return 200, store.get_item(unquote(encoded))
            if self.command == "POST" and path == "/items":
                sku, quantity = self._fields(self._body(), ("sku", "quantity"))
                return 201, store.add_item(sku, quantity)
            if self.command == "POST" and path == "/reservations":
                key, sku, quantity = self._fields(
                    self._body(), ("idempotency_key", "sku", "quantity")
                )
                return 201, store.reserve(key, sku, quantity)
            prefix, suffix = "/reservations/", "/release"
            if self.command == "POST" and path.startswith(prefix) and path.endswith(suffix):
                identifier = path[len(prefix):-len(suffix)]
                if not identifier or "/" in identifier:
                    raise NotFound("route not found")
                self._body()
                try:
                    reservation_id = int(identifier)
                except ValueError:
                    raise ValueError("reservation id must be a positive integer") from None
                return 200, store.release(reservation_id)
            raise NotFound("route not found")

        def _handle(self):
            try:
                raw_length = self.headers.get("Content-Length")
                if raw_length is not None:
                    try:
                        declared_length = int(raw_length)
                    except ValueError:
                        raise ValueError("invalid Content-Length") from None
                    if declared_length < 0:
                        raise ValueError("invalid Content-Length")
                    if declared_length > MAX_BODY:
                        self.close_connection = True
                        raise OverflowError("request body too large")
                status, value = self._dispatch()
                self._send(status, value)
            except OverflowError as error:
                self._error(413, error)
            except ValueError as error:
                self._error(400, error)
            except NotFound as error:
                self._error(404, error)
            except Conflict as error:
                self._error(409, error)
            except Exception:
                self._error(500, "internal server error")

        do_GET = _handle
        do_POST = _handle
        do_PUT = _handle
        do_DELETE = _handle
        do_PATCH = _handle

    return ThreadingHTTPServer((host, port), Handler)
