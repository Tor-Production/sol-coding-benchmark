"""Standard-library JSON HTTP API."""

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from .store import Conflict, NotFound, Store


_MAX_BODY = 64 * 1024


def create_server(db_path, host="127.0.0.1", port=0):
    store = Store(db_path)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def _send(self, status, value):
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
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
                raise OverflowError("request body exceeds 64 KiB")
            try:
                value = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ValueError("malformed JSON") from None
            if not isinstance(value, dict):
                raise ValueError("JSON body must be an object")
            return value

        @staticmethod
        def _required(body, *names):
            missing = [name for name in names if name not in body]
            if missing:
                raise ValueError("missing field: " + missing[0])
            return (body[name] for name in names)

        def _dispatch_get(self):
            path = urlsplit(self.path).path
            if path == "/health":
                return 200, {"ok": True}
            if path == "/report":
                return 200, store.report()
            match = re.fullmatch(r"/items/([^/]+)", path)
            if match:
                return 200, store.get_item(unquote(match.group(1)))
            raise NotFound("route not found")

        def _dispatch_post(self):
            path = urlsplit(self.path).path
            if path == "/items":
                body = self._json_body()
                sku, quantity = self._required(body, "sku", "quantity")
                return 201, store.add_item(sku, quantity)
            if path == "/reservations":
                body = self._json_body()
                key, sku, quantity = self._required(
                    body, "idempotency_key", "sku", "quantity"
                )
                return 201, store.reserve(key, sku, quantity)
            match = re.fullmatch(r"/reservations/([^/]+)/release", path)
            if match:
                self._json_body()
                try:
                    reservation_id = int(unquote(match.group(1)))
                except ValueError:
                    raise ValueError("reservation id must be a positive integer") from None
                return 200, store.release(reservation_id)
            raise NotFound("route not found")

        def _handle(self, dispatch):
            try:
                status, value = dispatch()
                self._send(status, value)
            except OverflowError as exc:
                self._error(413, exc)
            except (ValueError, TypeError) as exc:
                self._error(400, exc)
            except Conflict as exc:
                self._error(409, exc)
            except NotFound as exc:
                self._error(404, exc)
            except Exception:
                self._error(500, "internal server error")

        def do_GET(self):
            self._handle(self._dispatch_get)

        def do_POST(self):
            self._handle(self._dispatch_post)

        def _unknown_method(self):
            self._error(404, "route not found")

        do_DELETE = _unknown_method
        do_HEAD = _unknown_method
        do_OPTIONS = _unknown_method
        do_PATCH = _unknown_method
        do_PUT = _unknown_method

    return ThreadingHTTPServer((host, port), Handler)
