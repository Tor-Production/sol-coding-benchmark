"""Small JSON HTTP interface to the inventory store."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
from urllib.parse import unquote, urlsplit

from .store import Conflict, NotFound, Store


MAX_BODY = 64 * 1024


def create_server(db_path, host="127.0.0.1", port=0):
    store = Store(db_path)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def _send(self, status, record):
            body = json.dumps(record).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_error(self, code, message=None, explain=None):
            # BaseHTTPRequestHandler uses this for unsupported HTTP methods
            # and malformed request lines; keep its errors in the JSON format.
            self._send(404 if code == 501 else code,
                       {"error": message or self.responses.get(code, ("HTTP error",))[0]})

        @staticmethod
        def _invalid_constant(value):
            raise ValueError(f"Invalid JSON constant: {value}")

        def _body(self):
            lengths = self.headers.get_all("Content-Length", [])
            if self.headers.get("Transfer-Encoding") or len(lengths) != 1:
                raise ValueError("A single Content-Length is required")
            length = lengths[0]
            if not re.fullmatch(r"[0-9]+", length):
                raise ValueError("Invalid Content-Length")
            digits = length.lstrip("0") or "0"
            if len(digits) > len(str(MAX_BODY)) or (len(digits) == len(str(MAX_BODY))
                                                   and digits > str(MAX_BODY)):
                self._send(413, {"error": "Request body exceeds 64 KiB"})
                return None
            length = int(digits)
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"), parse_constant=self._invalid_constant)
            except (UnicodeError, ValueError, RecursionError) as exc:
                raise ValueError("Malformed JSON") from exc
            if not isinstance(data, dict):
                raise ValueError("Request body must be a JSON object")
            return data

        def _dispatch(self):
            try:
                path = urlsplit(self.path).path
                if self.command == "GET":
                    if path == "/health":
                        self._send(200, {"ok": True})
                    elif path == "/report":
                        self._send(200, store.report())
                    elif path.startswith("/items/") and "/" not in path[len("/items/"):]:
                        self._send(200, store.get_item(unquote(path[len("/items/"):], errors="strict")))
                    else:
                        raise NotFound("Unknown route")
                elif self.command == "POST":
                    release = re.fullmatch(r"/reservations/([^/]+)/release", path)
                    if path not in ("/items", "/reservations") and release is None:
                        raise NotFound("Unknown route")
                    data = self._body()
                    if data is None:
                        return
                    if path == "/items":
                        self._send(201, store.add_item(data["sku"], data["quantity"]))
                    elif path == "/reservations":
                        self._send(201, store.reserve(data["idempotency_key"], data["sku"], data["quantity"]))
                    else:
                        value = release.group(1)
                        if not re.fullmatch(r"[0-9]+", value):
                            raise ValueError("reservation_id must be a positive integer")
                        self._send(200, store.release(int(value)))
                else:
                    raise NotFound("Unknown route")
            except NotFound as exc:
                self._send(404, {"error": str(exc)})
            except Conflict as exc:
                self._send(409, {"error": str(exc)})
            except KeyError as exc:
                self._send(400, {"error": f"Missing field: {exc.args[0]}"})
            except (ValueError, OverflowError) as exc:
                self._send(400, {"error": str(exc)})

        do_GET = _dispatch
        do_POST = _dispatch
        do_PUT = _dispatch
        do_DELETE = _dispatch
        do_PATCH = _dispatch
        do_HEAD = _dispatch
        do_OPTIONS = _dispatch

    return ThreadingHTTPServer((host, port), Handler)
