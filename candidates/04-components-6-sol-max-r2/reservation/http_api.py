"""Standard-library JSON HTTP interface for the reservation store."""

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from .store import Conflict, NotFound, Store


_MAX_BODY_BYTES = 64 * 1024


class _PayloadTooLarge(Exception):
    pass


def _reject_json_constant(_value):
    raise ValueError("malformed JSON")


def _required(body, field):
    try:
        return body[field]
    except KeyError as exc:
        raise ValueError(f"missing field: {field}") from exc


def create_server(db_path, host="127.0.0.1", port=0):
    """Create a bound HTTP server; the caller controls its serving thread."""
    store = Store(db_path)

    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, status, payload):
            content = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def _handle(self, action):
            try:
                status, payload = action()
            except _PayloadTooLarge as exc:
                self._send_json(413, {"error": str(exc)})
            except NotFound as exc:
                self._send_json(404, {"error": str(exc)})
            except Conflict as exc:
                self._send_json(409, {"error": str(exc)})
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
            except Exception:
                self._send_json(500, {"error": "internal server error"})
            else:
                self._send_json(status, payload)

        def _content_length(self):
            if self.headers.get("Transfer-Encoding") is not None:
                raise ValueError("Transfer-Encoding is not supported")
            values = self.headers.get_all("Content-Length", [])
            if not values:
                return None
            if len(values) != 1 or not values[0].isascii() or not values[0].isdecimal():
                raise ValueError("invalid Content-Length")
            digits = values[0].lstrip("0") or "0"
            limit = str(_MAX_BODY_BYTES)
            if len(digits) > len(limit) or (
                len(digits) == len(limit) and digits > limit
            ):
                raise _PayloadTooLarge("request body exceeds 64 KiB")
            return int(digits)

        def _read_object(self, length):
            if length is None:
                raise ValueError("Content-Length is required")
            data = self.rfile.read(length)
            if len(data) != length:
                raise ValueError("incomplete request body")
            try:
                body = json.loads(
                    data.decode("utf-8"), parse_constant=_reject_json_constant
                )
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("malformed JSON") from exc
            if not isinstance(body, dict):
                raise ValueError("JSON body must be an object")
            return body

        def _get(self):
            self._content_length()
            path = urlsplit(self.path).path
            if path == "/health":
                return 200, {"ok": True}
            if path == "/report":
                return 200, store.report()
            if path.startswith("/items/"):
                encoded_sku = path[len("/items/"):]
                if "/" in encoded_sku:
                    raise NotFound("route not found")
                sku = unquote(encoded_sku, encoding="utf-8", errors="strict")
                return 200, store.get_item(sku)
            raise NotFound("route not found")

        def _post(self):
            length = self._content_length()
            path = urlsplit(self.path).path
            if path == "/items":
                body = self._read_object(length)
                return 201, store.add_item(
                    _required(body, "sku"), _required(body, "quantity")
                )
            if path == "/reservations":
                body = self._read_object(length)
                return 201, store.reserve(
                    _required(body, "idempotency_key"),
                    _required(body, "sku"),
                    _required(body, "quantity"),
                )
            match = re.fullmatch(r"/reservations/([^/]+)/release", path)
            if match is not None:
                self._read_object(length)
                id_text = unquote(match.group(1), encoding="utf-8", errors="strict")
                if not id_text.isascii() or not id_text.isdecimal():
                    raise ValueError("reservation_id must be a positive integer")
                return 200, store.release(int(id_text))
            raise NotFound("route not found")

        def do_GET(self):
            self._handle(self._get)

        def do_POST(self):
            self._handle(self._post)

        def _unknown_method(self):
            self._handle(self._unknown_route)

        def _unknown_route(self):
            self._content_length()
            raise NotFound("route not found")

        do_PUT = _unknown_method
        do_PATCH = _unknown_method
        do_DELETE = _unknown_method
        do_OPTIONS = _unknown_method

    return ThreadingHTTPServer((host, port), Handler)
