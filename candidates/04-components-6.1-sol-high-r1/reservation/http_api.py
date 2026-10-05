"""JSON HTTP interface to the reservation store."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from urllib.parse import unquote, urlsplit

from .store import Conflict, NotFound, Store


MAX_BODY_BYTES = 64 * 1024


def _reject_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


class _RequestError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


def create_server(db_path, host="127.0.0.1", port=0):
    store = Store(db_path)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            # Keep the CLI's output reserved for JSON records.
            pass

        def _reply(self, status, record):
            body = json.dumps(record, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            self.wfile.write(body)

        def send_error(self, code, message=None, explain=None):
            if code == 501:
                code, message = 404, "Unknown route"
            self._reply(code, {"error": message or self.responses.get(code, ("Error",))[0]})

        def _body_length(self):
            if self.headers.get("Transfer-Encoding") is not None:
                raise _RequestError(400, "Transfer-Encoding is unsupported")
            lengths = self.headers.get_all("Content-Length", [])
            if not lengths:
                return 0
            if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
                raise _RequestError(400, "Invalid Content-Length")
            # Compare before integer conversion, including for very long headers.
            digits = lengths[0].lstrip("0") or "0"
            if len(digits) > 5 or int(digits) > MAX_BODY_BYTES:
                raise _RequestError(413, "Request body exceeds 64 KiB")
            return int(digits)

        def _json_body(self, length):
            try:
                body = self.rfile.read(length)
                if len(body) != length:
                    raise ValueError("Incomplete request body")
                value = json.loads(body.decode("utf-8"), parse_constant=_reject_constant)
            except (ValueError, RecursionError) as error:
                raise _RequestError(400, "Malformed JSON") from error
            if not isinstance(value, dict):
                raise _RequestError(400, "JSON body must be an object")
            return value

        def _dispatch(self):
            try:
                length = self._body_length()
                path = urlsplit(self.path).path
                if self.command == "GET":
                    if path == "/health":
                        status, record = 200, {"ok": True}
                    elif path == "/report":
                        status, record = 200, store.report()
                    elif path.startswith("/items/") and "/" not in path[len("/items/"):]:
                        status, record = 200, store.get_item(
                            unquote(path[len("/items/"):], errors="strict")
                        )
                    else:
                        raise NotFound("Unknown route")
                elif self.command == "POST":
                    parts = path.split("/")
                    release_route = (len(parts) == 4 and parts[1] == "reservations"
                                     and parts[3] == "release")
                    if path not in ("/items", "/reservations") and not release_route:
                        raise NotFound("Unknown route")
                    body = self._json_body(length)
                    if path == "/items":
                        status, record = 201, store.add_item(body["sku"], body["quantity"])
                    elif path == "/reservations":
                        status, record = 201, store.reserve(
                            body["idempotency_key"], body["sku"], body["quantity"]
                        )
                    else:
                        status, record = 200, store.release(int(parts[2]))
                else:
                    raise NotFound("Unknown route")
            except _RequestError as error:
                status, record = error.status, {"error": str(error)}
            except NotFound as error:
                status, record = 404, {"error": str(error)}
            except Conflict as error:
                status, record = 409, {"error": str(error)}
            except KeyError as error:
                status, record = 400, {"error": f"Missing field: {error.args[0]}"}
            except (ValueError, OverflowError) as error:
                status, record = 400, {"error": str(error)}
            self._reply(status, record)

        do_GET = _dispatch
        do_POST = _dispatch
        do_PUT = _dispatch
        do_DELETE = _dispatch
        do_PATCH = _dispatch
        do_HEAD = _dispatch
        do_OPTIONS = _dispatch

    return ThreadingHTTPServer((host, port), Handler)
