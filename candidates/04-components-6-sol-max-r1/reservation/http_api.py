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


def _invalid_constant(value):
    raise ValueError("malformed JSON")


def create_server(db_path, host="127.0.0.1", port=0):
    """Initialize the database and return a bound, concurrent HTTP server."""
    store = Store(db_path)

    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, status, record):
            data = json.dumps(record, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _read_object(self):
            lengths = self.headers.get_all("Content-Length")
            if lengths is None or len(lengths) != 1:
                raise ValueError("one Content-Length header is required")
            header = lengths[0].strip()
            if re.fullmatch(r"[0-9]+", header) is None:
                raise ValueError("invalid Content-Length")
            digits = header.lstrip("0") or "0"
            limit = str(MAX_BODY_BYTES)
            if len(digits) > len(limit) or (len(digits) == len(limit) and digits > limit):
                raise _PayloadTooLarge("request body exceeds 64 KiB")
            length = int(digits)
            data = self.rfile.read(length)
            if len(data) != length:
                raise ValueError("incomplete request body")
            try:
                body = json.loads(
                    data.decode("utf-8"), parse_constant=_invalid_constant
                )
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("malformed JSON") from exc
            if not isinstance(body, dict):
                raise ValueError("JSON body must be an object")
            return body

        def _dispatch(self, method):
            path = urlsplit(self.path).path
            if method == "GET":
                if path == "/health":
                    return 200, {"ok": True}
                if path == "/report":
                    return 200, store.report()
                if path.startswith("/items/"):
                    encoded_sku = path[len("/items/"):]
                    if encoded_sku and "/" not in encoded_sku:
                        return 200, store.get_item(unquote(encoded_sku, errors="strict"))
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
                parts = path.split("/")
                if len(parts) == 4 and parts[1] == "reservations" and parts[3] == "release":
                    id_text = unquote(parts[2], errors="strict")
                    if re.fullmatch(r"[0-9]+", id_text) is None:
                        raise ValueError("reservation_id must be a positive integer")
                    self._read_object()
                    return 200, store.release(int(id_text))
            raise NotFound("route not found")

        def _handle(self, method):
            try:
                status, record = self._dispatch(method)
            except _PayloadTooLarge as exc:
                status, record = 413, {"error": str(exc)}
            except NotFound as exc:
                status, record = 404, {"error": str(exc)}
            except Conflict as exc:
                status, record = 409, {"error": str(exc)}
            except ValueError as exc:
                status, record = 400, {"error": str(exc)}
            except Exception:
                status, record = 500, {"error": "internal server error"}
            self._send_json(status, record)

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def send_error(self, code, message=None, explain=None):
            # BaseHTTPRequestHandler calls this for malformed requests and
            # unsupported methods; keep those responses in the JSON protocol.
            if code == 501:
                code, message = 404, "route not found"
            self._send_json(code, {"error": message or "request error"})

        def log_message(self, format, *args):
            pass

    class Server(ThreadingHTTPServer):
        daemon_threads = True

    return Server((host, port), Handler)
