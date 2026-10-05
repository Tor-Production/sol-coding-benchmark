"""Small JSON HTTP interface for the reservation store."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from .store import Conflict, NotFound, Store


MAX_BODY = 64 * 1024


def create_server(db_path, host="127.0.0.1", port=0):
    store = Store(db_path)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, value):
            payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _body(self):
            length_text = self.headers.get("Content-Length")
            if length_text is None:
                raise ValueError("Content-Length is required")
            try:
                length = int(length_text)
            except ValueError as exc:
                raise ValueError("invalid Content-Length") from exc
            if length < 0:
                raise ValueError("invalid Content-Length")
            if length > MAX_BODY:
                raise OverflowError("request body exceeds 64 KiB")
            data = self.rfile.read(length)
            if len(data) != length:
                raise ValueError("incomplete request body")
            try:
                value = json.loads(data.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise ValueError("malformed JSON") from exc
            if not isinstance(value, dict):
                raise ValueError("JSON request must be an object")
            return value

        def _handle(self):
            try:
                path = urlsplit(self.path).path
                if self.command == "GET":
                    if path == "/health":
                        return self._send(200, {"ok": True})
                    if path == "/report":
                        return self._send(200, store.report())
                    if path.startswith("/items/") and path != "/items/":
                        return self._send(200, store.get_item(unquote(path[len("/items/"):])))
                elif self.command == "POST":
                    if path == "/items":
                        body = self._body()
                        return self._send(201, store.add_item(body["sku"], body["quantity"]))
                    if path == "/reservations":
                        body = self._body()
                        return self._send(201, store.reserve(
                            body["idempotency_key"], body["sku"], body["quantity"]))
                    parts = path.split("/")
                    if len(parts) == 4 and parts[1] == "reservations" and parts[3] == "release":
                        body = self._body()
                        try:
                            reservation_id = int(parts[2])
                        except ValueError as exc:
                            raise ValueError("invalid reservation_id") from exc
                        return self._send(200, store.release(reservation_id))
                self._send(404, {"error": "route not found"})
            except KeyError as exc:
                self._send(400, {"error": f"missing field: {exc.args[0]}"})
            except ValueError as exc:
                self._send(400, {"error": str(exc)})
            except OverflowError as exc:
                self.close_connection = True
                self._send(413, {"error": str(exc)})
            except NotFound as exc:
                self._send(404, {"error": str(exc)})
            except Conflict as exc:
                self._send(409, {"error": str(exc)})

        def do_GET(self):
            self._handle()

        def do_POST(self):
            self._handle()

        def log_message(self, format, *args):
            pass

    return ThreadingHTTPServer((host, port), Handler)
