"""A JSON HTTP interface to the inventory store."""

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from .store import Conflict, NotFound, Store


MAX_BODY_BYTES = 64 * 1024


class _BodyTooLarge(Exception):
    pass


class _Server(ThreadingHTTPServer):
    request_queue_size = 128


def _reject_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


def _require_fields(data, *fields):
    for field in fields:
        if field not in data:
            raise ValueError(f"Missing field: {field}")


def create_server(db_path, host="127.0.0.1", port=0):
    store = Store(db_path)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            # Keep command output and error responses free of HTTP access logs.
            pass

        def _send_json(self, status, payload):
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            if self.command != "HEAD":
                self.wfile.write(body)

        def send_error(self, code, message=None, explain=None):
            # BaseHTTPRequestHandler also uses this for unsupported methods and
            # malformed HTTP. Keep those responses in the same JSON format.
            if code == 501:
                code, message = 404, "Route not found"
            self._send_json(code, {"error": message or self.responses.get(code, ("Error",))[0]})

        def _body_length(self):
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) > 1:
                raise ValueError("Content-Length must appear only once")
            value = lengths[0].strip() if lengths else "0"
            if not value.isascii() or not value.isdecimal():
                raise ValueError("Content-Length must be a nonnegative integer")
            # Check the size before reading, even for a very large decimal header.
            value = value.lstrip("0") or "0"
            if len(value) > len(str(MAX_BODY_BYTES)) or int(value) > MAX_BODY_BYTES:
                raise _BodyTooLarge
            if self.headers.get("Transfer-Encoding") is not None:
                raise ValueError("Transfer-Encoding is not supported; use Content-Length")
            return int(value)

        def _json_object(self, length):
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError("Incomplete request body")
            data = json.loads(body.decode("utf-8"), parse_constant=_reject_constant)
            if not isinstance(data, dict):
                raise ValueError("Request body must be a JSON object")
            return data

        def _dispatch(self):
            length = self._body_length()
            path = urlsplit(self.path).path
            if self.command == "GET":
                if path == "/health":
                    return 200, {"ok": True}
                if path == "/report":
                    return 200, store.report()
                if path.startswith("/items/"):
                    encoded_sku = path[len("/items/"):]
                    if encoded_sku and "/" not in encoded_sku:
                        return 200, store.get_item(unquote(encoded_sku, errors="strict"))
            elif self.command == "POST":
                if path == "/items":
                    data = self._json_object(length)
                    _require_fields(data, "sku", "quantity")
                    return 201, store.add_item(data["sku"], data["quantity"])
                if path == "/reservations":
                    data = self._json_object(length)
                    _require_fields(data, "idempotency_key", "sku", "quantity")
                    return 201, store.reserve(data["idempotency_key"], data["sku"], data["quantity"])
                match = re.fullmatch(r"/reservations/([^/]+)/release", path)
                if match:
                    self._json_object(length)
                    reservation_id = match.group(1)
                    if not reservation_id.isascii() or not reservation_id.isdecimal():
                        raise ValueError("reservation_id must be a positive integer")
                    return 200, store.release(int(reservation_id))
            return 404, {"error": "Route not found"}

        def _handle_request(self):
            try:
                status, payload = self._dispatch()
            except _BodyTooLarge:
                status, payload = 413, {"error": "Request body exceeds 64 KiB"}
            except NotFound as error:
                status, payload = 404, {"error": str(error)}
            except Conflict as error:
                status, payload = 409, {"error": str(error)}
            except (ValueError, RecursionError) as error:
                status, payload = 400, {"error": str(error)}
            except Exception:
                status, payload = 500, {"error": "Internal server error"}
            self._send_json(status, payload)

        do_GET = _handle_request
        do_POST = _handle_request

    return _Server((host, port), Handler)
