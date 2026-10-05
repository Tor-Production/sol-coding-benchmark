"""Standard-library HTTP interface for the reservation store."""

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from .store import Conflict, NotFound, Store


_MAX_BODY = 64 * 1024
_RELEASE_PATH = re.compile(r"/reservations/([^/]+)/release\Z")


class _PayloadTooLarge(Exception):
    pass


def _invalid_json_constant(token):
    raise ValueError(f"invalid JSON constant: {token}")


def create_server(db_path, host="127.0.0.1", port=0):
    store = Store(db_path)

    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, status, value):
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json_object(self):
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1:
                raise ValueError("one Content-Length header is required")
            if self.headers.get("Transfer-Encoding") is not None:
                raise ValueError("Transfer-Encoding is not supported")
            try:
                length = int(lengths[0])
            except ValueError as exc:
                raise ValueError("invalid Content-Length") from exc
            if length < 0:
                raise ValueError("invalid Content-Length")
            if length > _MAX_BODY:
                raise _PayloadTooLarge("request body exceeds 64 KiB")
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("incomplete request body")
            try:
                value = json.loads(raw.decode("utf-8"), parse_constant=_invalid_json_constant)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("malformed JSON") from exc
            if not isinstance(value, dict):
                raise ValueError("JSON body must be an object")
            return value

        @staticmethod
        def _field(obj, name):
            try:
                return obj[name]
            except KeyError as exc:
                raise ValueError(f"missing field: {name}") from exc

        def _dispatch(self):
            path = urlsplit(self.path).path
            if self.command == "GET":
                if path == "/health":
                    return 200, {"ok": True}
                if path == "/report":
                    return 200, store.report()
                item_path = path[len("/items/"):] if path.startswith("/items/") else ""
                if item_path and "/" not in item_path:
                    sku = unquote(item_path, errors="strict")
                    return 200, store.get_item(sku)
            elif self.command == "POST":
                if path == "/items":
                    obj = self._read_json_object()
                    return 201, store.add_item(
                        self._field(obj, "sku"), self._field(obj, "quantity")
                    )
                if path == "/reservations":
                    obj = self._read_json_object()
                    return 201, store.reserve(
                        self._field(obj, "idempotency_key"),
                        self._field(obj, "sku"),
                        self._field(obj, "quantity"),
                    )
                match = _RELEASE_PATH.fullmatch(path)
                if match:
                    obj = self._read_json_object()
                    if obj:
                        raise ValueError("release body must be an empty object")
                    if not re.fullmatch(r"[0-9]+", match.group(1)):
                        raise ValueError("reservation_id must be a positive integer")
                    return 200, store.release(int(match.group(1)))
            raise NotFound("route not found")

        def _handle(self):
            try:
                for declared_length in self.headers.get_all("Content-Length", []):
                    try:
                        length = int(declared_length)
                    except ValueError:
                        continue
                    if length > _MAX_BODY:
                        raise _PayloadTooLarge("request body exceeds 64 KiB")
                status, value = self._dispatch()
            except _PayloadTooLarge as exc:
                status, value = 413, {"error": str(exc)}
            except NotFound as exc:
                status, value = 404, {"error": str(exc)}
            except Conflict as exc:
                status, value = 409, {"error": str(exc)}
            except ValueError as exc:
                status, value = 400, {"error": str(exc)}
            except Exception:
                status, value = 500, {"error": "internal server error"}
            self._send_json(status, value)

        def do_GET(self):
            self._handle()

        def do_POST(self):
            self._handle()

        def do_PUT(self):
            self._handle()

        def do_PATCH(self):
            self._handle()

        def do_DELETE(self):
            self._handle()

    return ThreadingHTTPServer((host, port), Handler)
