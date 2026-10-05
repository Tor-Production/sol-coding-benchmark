"""Standard-library HTTP API for the reservation store."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
from urllib.parse import unquote, urlsplit

from .store import Conflict, NotFound, Store


_MAX_BODY_BYTES = 64 * 1024
_RELEASE_PATH = re.compile(r"^/reservations/([^/]+)/release$")


class _PayloadTooLarge(Exception):
    pass


def create_server(db_path, host="127.0.0.1", port=0):
    """Create and bind a threaded HTTP server without starting its loop."""

    store = Store(db_path)

    class RequestHandler(BaseHTTPRequestHandler):
        def _send_json(self, status, value):
            body = json.dumps(
                value, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_error_json(self, status, error):
            self._send_json(status, {"error": str(error)})

        def _read_json_object(self):
            header = self.headers.get("Content-Length")
            if header is None:
                raise ValueError("Content-Length is required")
            try:
                length = int(header)
            except (TypeError, ValueError):
                raise ValueError("invalid Content-Length") from None
            if length < 0:
                raise ValueError("invalid Content-Length")
            if length > _MAX_BODY_BYTES:
                raise _PayloadTooLarge("request body exceeds 64 KiB")

            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError("incomplete request body")
            try:
                value = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ValueError("malformed JSON") from None
            if not isinstance(value, dict):
                raise ValueError("JSON body must be an object")
            return value

        @staticmethod
        def _field(value, name):
            if name not in value:
                raise ValueError(f"missing field: {name}")
            return value[name]

        def _run(self, operation):
            try:
                status, result = operation()
                self._send_json(status, result)
            except _PayloadTooLarge as error:
                self._send_error_json(413, error)
            except NotFound as error:
                self._send_error_json(404, error)
            except Conflict as error:
                self._send_error_json(409, error)
            except (ValueError, TypeError) as error:
                self._send_error_json(400, error)
            except Exception:
                # A bad request or unexpected storage failure must not terminate
                # the serving loop or leak implementation details to the client.
                self._send_error_json(500, "internal server error")

        def do_GET(self):
            def operation():
                path = urlsplit(self.path).path
                if path == "/health":
                    return 200, {"ok": True}
                if path == "/report":
                    return 200, store.report()
                if path.startswith("/items/"):
                    sku = unquote(path[len("/items/") :])
                    return 200, store.get_item(sku)
                raise NotFound("route not found")

            self._run(operation)

        def do_POST(self):
            def operation():
                path = urlsplit(self.path).path
                if path == "/items":
                    data = self._read_json_object()
                    result = store.add_item(
                        self._field(data, "sku"), self._field(data, "quantity")
                    )
                    return 201, result
                if path == "/reservations":
                    data = self._read_json_object()
                    result = store.reserve(
                        self._field(data, "idempotency_key"),
                        self._field(data, "sku"),
                        self._field(data, "quantity"),
                    )
                    return 201, result

                match = _RELEASE_PATH.fullmatch(path)
                if match is not None:
                    self._read_json_object()
                    try:
                        reservation_id = int(match.group(1))
                    except ValueError:
                        raise ValueError(
                            "reservation_id must be a positive integer"
                        ) from None
                    return 200, store.release(reservation_id)
                raise NotFound("route not found")

            self._run(operation)

        def _unknown_method(self):
            self._send_error_json(404, "route not found")

        do_DELETE = _unknown_method
        do_HEAD = _unknown_method
        do_OPTIONS = _unknown_method
        do_PATCH = _unknown_method
        do_PUT = _unknown_method

        def log_message(self, format, *args):
            return

    return ThreadingHTTPServer((host, port), RequestHandler)
