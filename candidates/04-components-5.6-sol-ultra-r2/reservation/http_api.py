"""Standard-library HTTP interface for the reservation store."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
from urllib.parse import unquote, urlsplit

from .store import Conflict, NotFound, Store


_MAX_REQUEST_BODY = 64 * 1024
_ITEM_PATH = re.compile(r"/items/([^/]+)\Z")
_RELEASE_PATH = re.compile(r"/reservations/([^/]+)/release\Z")
_BAD_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")


class _RequestError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _decode_path_segment(segment):
    if _BAD_PERCENT_ESCAPE.search(segment):
        raise _RequestError(400, "malformed URL encoding")
    try:
        return unquote(segment, encoding="utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise _RequestError(400, "malformed URL encoding") from exc


def _reject_json_constant(value):
    raise ValueError(f"invalid JSON constant: {value}")


def create_server(db_path, host="127.0.0.1", port=0):
    """Create and bind a threaded HTTP server backed by *db_path*."""

    store = Store(db_path)

    class ReservationHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            # Library users decide how (or whether) request logging is handled.
            return

        def _send_json(self, status, payload, *, include_body=True):
            body = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if include_body:
                self.wfile.write(body)

        def _path(self):
            try:
                return urlsplit(self.path).path
            except ValueError as exc:
                raise _RequestError(400, "malformed request path") from exc

        def _read_json_object(self):
            if self.headers.get("Transfer-Encoding") is not None:
                self.close_connection = True
                raise _RequestError(400, "chunked request bodies are not supported")

            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1:
                self.close_connection = True
                raise _RequestError(400, "exactly one Content-Length is required")
            try:
                length = int(lengths[0], 10)
            except (TypeError, ValueError) as exc:
                self.close_connection = True
                raise _RequestError(400, "invalid Content-Length") from exc
            if length < 0:
                self.close_connection = True
                raise _RequestError(400, "invalid Content-Length")
            if length > _MAX_REQUEST_BODY:
                self.close_connection = True
                raise _RequestError(413, "request body exceeds 64 KiB")

            raw = self.rfile.read(length)
            if len(raw) != length:
                self.close_connection = True
                raise _RequestError(400, "incomplete request body")
            try:
                text = raw.decode("utf-8")
                value = json.loads(text, parse_constant=_reject_json_constant)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                raise _RequestError(400, "malformed JSON") from exc
            if not isinstance(value, dict):
                raise _RequestError(400, "JSON body must be an object")
            return value

        @staticmethod
        def _required(body, name):
            if name not in body:
                raise _RequestError(400, f"missing field: {name}")
            return body[name]

        def _dispatch_get(self):
            path = self._path()
            if path == "/health":
                return 200, {"ok": True}
            if path == "/report":
                return 200, store.report()

            match = _ITEM_PATH.fullmatch(path)
            if match is not None:
                sku = _decode_path_segment(match.group(1))
                return 200, store.get_item(sku)
            raise _RequestError(404, "route not found")

        def _dispatch_post(self):
            path = self._path()
            if path == "/items":
                body = self._read_json_object()
                record = store.add_item(
                    self._required(body, "sku"),
                    self._required(body, "quantity"),
                )
                return 201, record
            if path == "/reservations":
                body = self._read_json_object()
                record = store.reserve(
                    self._required(body, "idempotency_key"),
                    self._required(body, "sku"),
                    self._required(body, "quantity"),
                )
                return 201, record

            match = _RELEASE_PATH.fullmatch(path)
            if match is not None:
                body = self._read_json_object()
                del body  # The route requires an object but has no fields.
                raw_id = _decode_path_segment(match.group(1))
                try:
                    reservation_id = int(raw_id, 10)
                except ValueError as exc:
                    raise _RequestError(
                        400, "reservation id must be a positive integer"
                    ) from exc
                return 200, store.release(reservation_id)
            raise _RequestError(404, "route not found")

        def _handle(self, method):
            try:
                if method == "GET":
                    status, payload = self._dispatch_get()
                else:
                    status, payload = self._dispatch_post()
            except _RequestError as exc:
                status, payload = exc.status, {"error": str(exc)}
            except NotFound as exc:
                status, payload = 404, {"error": str(exc)}
            except Conflict as exc:
                status, payload = 409, {"error": str(exc)}
            except ValueError as exc:
                status, payload = 400, {"error": str(exc)}
            except Exception:
                status, payload = 500, {"error": "internal server error"}

            try:
                self._send_json(status, payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def _unknown_method(self, *, include_body=True):
            self.close_connection = True
            try:
                self._send_json(
                    404, {"error": "route not found"}, include_body=include_body
                )
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_HEAD(self):
            self._unknown_method(include_body=False)

        do_DELETE = _unknown_method
        do_OPTIONS = _unknown_method
        do_PATCH = _unknown_method
        do_PUT = _unknown_method

    server = ThreadingHTTPServer((host, port), ReservationHandler)
    server.store = store
    return server
