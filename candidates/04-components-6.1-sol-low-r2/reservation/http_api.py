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

        def _send(self, status, record):
            body = json.dumps(record, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            if self.headers.get("Transfer-Encoding") is not None:
                raise ValueError("Transfer-Encoding is unsupported")
            length = self.headers.get("Content-Length")
            if length is None or not length.isascii() or not length.isdecimal():
                raise ValueError("Invalid Content-Length")
            length = int(length)
            if length > 64 * 1024:
                self._send(413, {"error": "Request body exceeds 64 KiB"})
                return None
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("Incomplete request body")
            try:
                body = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeError, RecursionError) as exc:
                raise ValueError("Malformed JSON") from exc
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
                    if path.startswith("/items/") and path[len("/items/"):]:
                        return self._send(200, store.get_item(unquote(path[len("/items/"):], errors="strict")))
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
                        identifier = release.group(1)
                        if not identifier.isascii() or not identifier.isdecimal():
                            raise ValueError("reservation_id must be a positive integer")
                        return self._send(200, store.release(int(identifier)))
                self._send(404, {"error": "Route not found"})
            except NotFound as exc:
                self._send(404, {"error": str(exc)})
            except Conflict as exc:
                self._send(409, {"error": str(exc)})
            except KeyError as exc:
                self._send(400, {"error": f"Missing field: {exc.args[0]}"})
            except (ValueError, OverflowError) as exc:
                self._send(400, {"error": str(exc)})

        do_GET = _handle
        do_POST = _handle
        do_PUT = _handle
        do_DELETE = _handle
        do_PATCH = _handle
        do_OPTIONS = _handle
        do_HEAD = _handle
        do_TRACE = _handle
        do_CONNECT = _handle

    return ThreadingHTTPServer((host, port), Handler)
