"""Standard-library JSON HTTP interface for the reservation store."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
from urllib.parse import unquote, urlsplit

from .store import Conflict, NotFound, Store


MAX_BODY_BYTES = 64 * 1024


class _PayloadTooLarge(Exception):
    pass


def _required(body, name):
    if name not in body:
        raise ValueError(f"missing field: {name}")
    return body[name]


def create_server(db_path, host="127.0.0.1", port=0):
    """Return a bound, concurrent HTTP server using the given database."""
    store = Store(db_path)

    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, status, value):
            payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _read_object(self):
            lengths = self.headers.get_all("Content-Length", [])
            if not lengths:
                raise ValueError("Content-Length is required")
            if len(lengths) != 1:
                raise ValueError("invalid Content-Length")
            digits = lengths[0].strip()
            if not re.fullmatch(r"[0-9]+", digits):
                raise ValueError("invalid Content-Length")
            digits = digits.lstrip("0") or "0"
            limit = str(MAX_BODY_BYTES)
            if len(digits) > len(limit) or (len(digits) == len(limit) and digits > limit):
                raise _PayloadTooLarge("request body exceeds 64 KiB")
            length = int(digits)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("incomplete request body")
            try:
                body = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
                raise ValueError("malformed JSON") from exc
            if not isinstance(body, dict):
                raise ValueError("JSON body must be an object")
            return body

        def _route(self, method):
            path = urlsplit(self.path).path
            if method == "GET":
                if path == "/health":
                    return 200, {"ok": True}
                if path == "/report":
                    return 200, store.report()
                if path.startswith("/items/") and "/" not in path[len("/items/"):]:
                    sku = unquote(path[len("/items/"):], encoding="utf-8", errors="strict")
                    return 200, store.get_item(sku)
            elif method == "POST":
                if path == "/items":
                    body = self._read_object()
                    return 201, store.add_item(
                        _required(body, "sku"), _required(body, "quantity")
                    )
                if path == "/reservations":
                    body = self._read_object()
                    return 201, store.reserve(
                        _required(body, "idempotency_key"),
                        _required(body, "sku"),
                        _required(body, "quantity"),
                    )
                match = re.fullmatch(r"/reservations/([^/]+)/release", path)
                if match:
                    raw_id = match.group(1)
                    if not re.fullmatch(r"[0-9]+", raw_id):
                        raise ValueError("reservation_id must be a positive integer")
                    self._read_object()
                    return 200, store.release(int(raw_id))
            raise NotFound("route not found")

        def _dispatch(self, method):
            try:
                status, body = self._route(method)
            except _PayloadTooLarge as exc:
                status, body = 413, {"error": str(exc)}
            except Conflict as exc:
                status, body = 409, {"error": str(exc)}
            except NotFound as exc:
                status, body = 404, {"error": str(exc)}
            except ValueError as exc:
                status, body = 400, {"error": str(exc)}
            except Exception:
                status, body = 500, {"error": "internal server error"}
            self._send_json(status, body)

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_PUT(self):
            self._dispatch("PUT")

        def do_PATCH(self):
            self._dispatch("PATCH")

        def do_DELETE(self):
            self._dispatch("DELETE")

        def log_message(self, format, *args):
            pass

    return ThreadingHTTPServer((host, port), Handler)
