"""Small JSON HTTP interface to the reservation store."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from urllib.parse import unquote, urlsplit

from .store import Conflict, NotFound, Store


MAX_BODY = 64 * 1024


def create_server(db_path, host="127.0.0.1", port=0):
    store = Store(db_path)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, value):
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

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
                raise _TooLarge("request body exceeds 64 KiB")
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("incomplete request body")
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("malformed JSON") from exc
            if not isinstance(value, dict):
                raise ValueError("JSON body must be an object")
            return value

        def _handle(self):
            path = urlsplit(self.path).path
            try:
                if self.command == "GET":
                    if path == "/health":
                        return self._send(200, {"ok": True})
                    if path == "/report":
                        return self._send(200, store.report())
                    if path.startswith("/items/") and path.count("/") == 2:
                        sku = unquote(path[len("/items/"):])
                        return self._send(200, store.get_item(sku))
                elif self.command == "POST":
                    if path == "/items":
                        body = self._body()
                        return self._send(201, store.add_item(body["sku"], body["quantity"]))
                    if path == "/reservations":
                        body = self._body()
                        return self._send(
                            201,
                            store.reserve(
                                body["idempotency_key"], body["sku"], body["quantity"]
                            ),
                        )
                    parts = path.split("/")
                    if len(parts) == 4 and parts[1] == "reservations" and parts[3] == "release":
                        self._body()
                        try:
                            reservation_id = int(parts[2])
                        except ValueError as exc:
                            raise ValueError("reservation_id must be a positive integer") from exc
                        return self._send(200, store.release(reservation_id))
                return self._send(404, {"error": "route not found"})
            except _TooLarge as exc:
                self._send(413, {"error": str(exc)})
            except KeyError as exc:
                self._send(400, {"error": f"missing field: {exc.args[0]}"})
            except ValueError as exc:
                self._send(400, {"error": str(exc)})
            except NotFound as exc:
                self._send(404, {"error": str(exc)})
            except Conflict as exc:
                self._send(409, {"error": str(exc)})

        def do_GET(self):
            self._handle()

        def do_POST(self):
            self._handle()

        def do_PUT(self):
            self._handle()

        def do_DELETE(self):
            self._handle()

        def do_PATCH(self):
            self._handle()

        def do_OPTIONS(self):
            self._handle()

    return ThreadingHTTPServer((host, port), Handler)


class _TooLarge(Exception):
    pass
