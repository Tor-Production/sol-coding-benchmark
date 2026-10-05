"""Standard-library HTTP API for the reservation store."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from urllib.parse import unquote, urlsplit

from .store import Conflict, NotFound, Store


_MAX_BODY_BYTES = 64 * 1024


class _PayloadTooLarge(Exception):
    pass


def _reject_json_constant(value):
    raise ValueError(f"invalid JSON constant: {value}")


class _ReservationServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128


class _Handler(BaseHTTPRequestHandler):
    server_version = "ReservationHTTP/1.0"

    def log_message(self, format, *args):
        # Applications embedding the returned server can add their own logging.
        return

    def _send_json(self, status, payload):
        body = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _request_path(self):
        return urlsplit(self.path).path

    def _read_json_object(self):
        if self.headers.get("Transfer-Encoding") is not None:
            self.close_connection = True
            raise ValueError("Transfer-Encoding is not supported")

        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1:
            self.close_connection = True
            raise ValueError("exactly one Content-Length header is required")
        raw_length = lengths[0].strip()
        if not raw_length or not raw_length.isascii() or not raw_length.isdigit():
            self.close_connection = True
            raise ValueError("invalid Content-Length")
        length = int(raw_length)
        if length > _MAX_BODY_BYTES:
            self.close_connection = True
            raise _PayloadTooLarge("request body exceeds 64 KiB")

        raw_body = self.rfile.read(length)
        if len(raw_body) != length:
            self.close_connection = True
            raise ValueError("incomplete request body")
        value = json.loads(
            raw_body.decode("utf-8"), parse_constant=_reject_json_constant
        )
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    @staticmethod
    def _required(body, field):
        if field not in body:
            raise ValueError(f"missing field: {field}")
        return body[field]

    def _dispatch_get(self):
        path = self._request_path()
        if path == "/health":
            return 200, {"ok": True}
        if path == "/report":
            return 200, self.server.store.report()
        if path.startswith("/items/"):
            encoded_sku = path[len("/items/") :]
            sku = unquote(encoded_sku, encoding="utf-8", errors="strict")
            return 200, self.server.store.get_item(sku)
        raise NotFound("route not found")

    def _dispatch_post(self):
        path = self._request_path()
        if path == "/items":
            body = self._read_json_object()
            record = self.server.store.add_item(
                self._required(body, "sku"),
                self._required(body, "quantity"),
            )
            return 201, record

        if path == "/reservations":
            body = self._read_json_object()
            record = self.server.store.reserve(
                self._required(body, "idempotency_key"),
                self._required(body, "sku"),
                self._required(body, "quantity"),
            )
            return 201, record

        parts = path.split("/")
        if (
            len(parts) == 4
            and parts[0] == ""
            and parts[1] == "reservations"
            and parts[2]
            and parts[3] == "release"
        ):
            self._read_json_object()
            try:
                reservation_id = int(parts[2])
            except ValueError as exc:
                raise ValueError("reservation id must be an integer") from exc
            return 200, self.server.store.release(reservation_id)

        raise NotFound("route not found")

    def _handle(self, dispatcher):
        try:
            status, payload = dispatcher()
        except _PayloadTooLarge as exc:
            status, payload = 413, {"error": str(exc)}
        except NotFound as exc:
            status, payload = 404, {"error": str(exc)}
        except Conflict as exc:
            status, payload = 409, {"error": str(exc)}
        except (ValueError, KeyError, TypeError, OverflowError, RecursionError) as exc:
            status, payload = 400, {"error": str(exc) or "invalid request"}
        except Exception:
            status, payload = 500, {"error": "internal server error"}

        try:
            self._send_json(status, payload)
        except OSError:
            self.close_connection = True

    def do_GET(self):
        self._handle(self._dispatch_get)

    def do_POST(self):
        self._handle(self._dispatch_post)

    def _dispatch_unknown(self):
        raise NotFound("route not found")

    def _handle_unknown_method(self):
        self._handle(self._dispatch_unknown)

    do_DELETE = _handle_unknown_method
    do_PATCH = _handle_unknown_method
    do_PUT = _handle_unknown_method


def create_server(db_path, host="127.0.0.1", port=0):
    """Create and bind a concurrent HTTP server without starting its loop."""

    store = Store(db_path)
    server = _ReservationServer((host, port), _Handler)
    server.store = store
    return server
