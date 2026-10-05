"""A JSON HTTP interface to the inventory store."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from urllib.parse import unquote, urlsplit

from .store import Conflict, NotFound, Store


_MAX_BODY = 64 * 1024


class _HTTPError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


def _reject_json_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


class _Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, format, *args):
        pass

    def _send_json(self, status, record):
        payload = json.dumps(record, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        if self.command != "HEAD":
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def send_error(self, code, message=None, explain=None):
        # BaseHTTPRequestHandler also uses this for malformed HTTP and unknown verbs.
        if code == 501:
            code, message = 404, "Unknown route"
        self._send_json(code, {"error": message or "Request failed"})

    def _content_length(self):
        if self.headers.get("Transfer-Encoding") is not None:
            raise ValueError("Transfer-Encoding is not supported")
        lengths = self.headers.get_all("Content-Length", [])
        if not lengths:
            return 0
        if len(lengths) != 1:
            raise ValueError("Expected one Content-Length header")
        value = lengths[0].strip()
        if not value or any(char < "0" or char > "9" for char in value):
            raise ValueError("Content-Length must be a nonnegative integer")
        value = value.lstrip("0") or "0"
        limit = str(_MAX_BODY)
        if len(value) > len(limit) or (len(value) == len(limit) and value > limit):
            raise _HTTPError(413, "Request body exceeds 64 KiB")
        return int(value)

    def _read_object(self, length):
        try:
            body = self.rfile.read(length)
        except (TimeoutError, OSError) as error:
            raise ValueError("Incomplete request body") from error
        if len(body) != length:
            raise ValueError("Incomplete request body")
        try:
            record = json.loads(body.decode("utf-8"), parse_constant=_reject_json_constant)
        except (UnicodeDecodeError, ValueError, RecursionError) as error:
            raise ValueError("Malformed JSON") from error
        if not isinstance(record, dict):
            raise ValueError("Request JSON must be an object")
        return record

    @staticmethod
    def _require_fields(record, *fields):
        for field in fields:
            if field not in record:
                raise ValueError(f"Missing field: {field}")

    def _dispatch(self):
        length = self._content_length()
        path = urlsplit(self.path).path
        store = self.server.store
        if self.command == "GET":
            if path == "/health":
                return 200, {"ok": True}
            if path == "/report":
                return 200, store.report()
            if path.startswith("/items/"):
                sku = path[len("/items/"):]
                if "/" not in sku:
                    return 200, store.get_item(unquote(sku, errors="strict"))
        elif self.command == "POST":
            if path == "/items":
                record = self._read_object(length)
                self._require_fields(record, "sku", "quantity")
                return 201, store.add_item(record["sku"], record["quantity"])
            if path == "/reservations":
                record = self._read_object(length)
                self._require_fields(record, "idempotency_key", "sku", "quantity")
                return 201, store.reserve(
                    record["idempotency_key"], record["sku"], record["quantity"]
                )
            if path.startswith("/reservations/") and path.endswith("/release"):
                reservation_id = path[len("/reservations/"):-len("/release")]
                if reservation_id and "/" not in reservation_id:
                    self._read_object(length)
                    try:
                        reservation_id = int(unquote(reservation_id, errors="strict"))
                    except ValueError as error:
                        raise ValueError("reservation_id must be a positive integer") from error
                    return 200, store.release(reservation_id)
        raise _HTTPError(404, "Unknown route")

    def _handle(self):
        try:
            status, record = self._dispatch()
        except _HTTPError as error:
            status, record = error.status, {"error": str(error)}
        except NotFound as error:
            status, record = 404, {"error": str(error)}
        except Conflict as error:
            status, record = 409, {"error": str(error)}
        except ValueError as error:
            status, record = 400, {"error": str(error)}
        except Exception:
            status, record = 500, {"error": "Internal server error"}
        self._send_json(status, record)

    do_GET = _handle
    do_POST = _handle


def create_server(db_path, host="127.0.0.1", port=0):
    """Return a bound threaded HTTP server; the caller controls its lifecycle."""
    store = Store(db_path)
    server = ThreadingHTTPServer((host, port), _Handler)
    server.store = store
    return server
