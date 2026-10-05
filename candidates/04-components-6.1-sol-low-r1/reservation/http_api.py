import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from .store import Conflict, NotFound, Store


def create_server(db_path, host="127.0.0.1", port=0):
    store = Store(db_path)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def send_error(self, code, message=None, explain=None):
            # BaseHTTPRequestHandler uses 501 for unsupported HTTP methods.
            self._send(404 if code == 501 else code,
                       {"error": message or "Invalid request"})

        def _send(self, status, record):
            body = json.dumps(record, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise ValueError("Invalid Content-Length") from None
            if length < 0:
                raise ValueError("Invalid Content-Length")
            if length > 65536:
                self._send(413, {"error": "Request body exceeds 64 KiB"})
                return None
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeError):
                raise ValueError("Malformed JSON") from None
            if not isinstance(body, dict):
                raise ValueError("JSON body must be an object")
            return body

        def _handle(self):
            try:
                path = urlsplit(self.path).path
                if self.command == "GET":
                    if path == "/health":
                        return self._send(200, {"ok": True})
                    if path == "/report":
                        return self._send(200, store.report())
                    if path.startswith("/items/") and path.count("/") == 2:
                        return self._send(200, store.get_item(unquote(path[7:])))
                elif self.command == "POST":
                    release = re.fullmatch(r"/reservations/([^/]+)/release", path)
                    if path in ("/items", "/reservations") or release:
                        body = self._body()
                        if body is None:
                            return
                        if path == "/items":
                            return self._send(201, store.add_item(body["sku"], body["quantity"]))
                        if path == "/reservations":
                            return self._send(201, store.reserve(body["idempotency_key"], body["sku"], body["quantity"]))
                        if not re.fullmatch(r"[0-9]+", release[1]):
                            raise ValueError("reservation_id must be a positive integer")
                        return self._send(200, store.release(int(release[1])))
                self._send(404, {"error": "Route not found"})
            except NotFound as exc:
                self._send(404, {"error": str(exc)})
            except Conflict as exc:
                self._send(409, {"error": str(exc)})
            except (ValueError, KeyError) as exc:
                self._send(400, {"error": str(exc)})

        do_GET = _handle
        do_POST = _handle
        do_PUT = _handle
        do_DELETE = _handle
        do_PATCH = _handle
        do_HEAD = _handle
        do_OPTIONS = _handle

    return ThreadingHTTPServer((host, port), Handler)
