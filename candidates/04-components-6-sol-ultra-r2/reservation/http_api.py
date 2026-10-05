"""Standard-library HTTP interface for the reservation store."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from .store import Conflict, NotFound, Store


_MAX_BODY = 64 * 1024


class _PayloadTooLarge(Exception):
    pass


def create_server(db_path, host="127.0.0.1", port=0):
    store = Store(db_path)

    class Handler(BaseHTTPRequestHandler):
        @staticmethod
        def _reject_json_constant(value):
            raise ValueError(f"invalid JSON constant: {value}")

        def _send_json(self, status, record):
            body = json.dumps(record, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _content_length(self, required=False):
            values = self.headers.get_all("Content-Length", [])
            if not values:
                if required:
                    raise ValueError("Content-Length is required")
                return 0
            if len(values) != 1:
                raise ValueError("invalid Content-Length")
            value = values[0].strip()
            if not value or not value.isascii() or not value.isdigit():
                raise ValueError("invalid Content-Length")
            digits = value.lstrip("0") or "0"
            if len(digits) > 5 or (len(digits) == 5 and digits > str(_MAX_BODY)):
                raise _PayloadTooLarge("request body exceeds 64 KiB")
            return int(digits)

        def _read_object(self):
            length = self._content_length(required=True)
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError("incomplete request body")
            try:
                value = json.loads(body, parse_constant=self._reject_json_constant)
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise ValueError("malformed JSON") from exc
            if not isinstance(value, dict):
                raise ValueError("JSON body must be an object")
            return value

        def _path(self):
            return urlsplit(self.path).path

        def _get(self):
            self._content_length()
            path = self._path()
            if path == "/health":
                return 200, {"ok": True}
            if path == "/report":
                return 200, store.report()
            if path.startswith("/items/"):
                encoded_sku = path[len("/items/"):]
                if "/" not in encoded_sku:
                    return 200, store.get_item(unquote(encoded_sku, errors="strict"))
            raise NotFound("route not found")

        def _post(self):
            path = self._path()
            if path == "/items":
                data = self._read_object()
                return 201, store.add_item(data["sku"], data["quantity"])
            if path == "/reservations":
                data = self._read_object()
                return 201, store.reserve(
                    data["idempotency_key"], data["sku"], data["quantity"]
                )
            prefix = "/reservations/"
            suffix = "/release"
            if path.startswith(prefix) and path.endswith(suffix):
                text_id = path[len(prefix):-len(suffix)]
                if "/" not in text_id:
                    self._read_object()
                    try:
                        reservation_id = int(text_id)
                    except ValueError as exc:
                        raise ValueError("reservation_id must be a positive integer") from exc
                    return 200, store.release(reservation_id)
            self._content_length()
            raise NotFound("route not found")

        def _handle(self, operation):
            try:
                status, record = operation()
            except _PayloadTooLarge as exc:
                status, record = 413, {"error": str(exc)}
            except (ValueError, KeyError) as exc:
                message = f"missing field: {exc.args[0]}" if isinstance(exc, KeyError) else str(exc)
                status, record = 400, {"error": message}
            except NotFound as exc:
                status, record = 404, {"error": str(exc)}
            except Conflict as exc:
                status, record = 409, {"error": str(exc)}
            except Exception:
                status, record = 500, {"error": "internal server error"}
            self._send_json(status, record)

        def do_GET(self):
            self._handle(self._get)

        def do_POST(self):
            self._handle(self._post)

        def send_error(self, code, message=None, explain=None):
            # BaseHTTPRequestHandler otherwise emits HTML for unsupported methods.
            if code == 501:
                code = 404
            self._send_json(code, {"error": message or "request failed"})

    return ThreadingHTTPServer((host, port), Handler)
