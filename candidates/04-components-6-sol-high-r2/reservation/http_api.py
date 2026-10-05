"""JSON HTTP interface for the inventory store."""

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from .store import Conflict, NotFound, Store


MAX_BODY_BYTES = 64 * 1024
RELEASE_PATH = re.compile(r"/reservations/([^/]+)/release\Z")


def create_server(db_path, host="127.0.0.1", port=0):
    store = Store(db_path)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, data):
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            header = self.headers.get("Content-Length")
            try:
                length = int(header)
            except (TypeError, ValueError):
                raise ValueError("Content-Length must be a nonnegative integer")
            if length < 0:
                raise ValueError("Content-Length must be a nonnegative integer")
            if length > MAX_BODY_BYTES:
                raise _BodyTooLarge("request body exceeds 64 KiB")
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("incomplete request body")
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("malformed JSON") from exc
            if not isinstance(data, dict):
                raise ValueError("JSON body must be an object")
            return data

        def _dispatch(self):
            path = urlsplit(self.path).path
            if self.command == "GET":
                if path == "/health":
                    return 200, {"ok": True}
                if path == "/report":
                    return 200, store.report()
                if path.startswith("/items/") and path != "/items/" and "/" not in path[len("/items/"):]:
                    return 200, store.get_item(unquote(path[len("/items/"):]))
            elif self.command == "POST":
                if path == "/items":
                    body = self._body()
                    return 201, store.add_item(body["sku"], body["quantity"])
                if path == "/reservations":
                    body = self._body()
                    return 201, store.reserve(
                        body["idempotency_key"], body["sku"], body["quantity"]
                    )
                match = RELEASE_PATH.fullmatch(path)
                if match:
                    self._body()
                    try:
                        reservation_id = int(match.group(1))
                    except ValueError as exc:
                        raise ValueError("reservation_id must be a positive integer") from exc
                    return 200, store.release(reservation_id)
            raise NotFound("route not found")

        def _handle(self):
            try:
                status, data = self._dispatch()
            except _BodyTooLarge as exc:
                status, data = 413, {"error": str(exc)}
            except (ValueError, KeyError) as exc:
                status, data = 400, {"error": str(exc)}
            except NotFound as exc:
                status, data = 404, {"error": str(exc)}
            except Conflict as exc:
                status, data = 409, {"error": str(exc)}
            except Exception as exc:
                status, data = 500, {"error": str(exc)}
            self._send(status, data)

        def do_GET(self):
            self._handle()

        def do_POST(self):
            self._handle()

        def do_PUT(self):
            self._handle()

        def do_PATCH(self):
            self._handle()

        def do_DELETE(self):
            self._handle()

        def do_OPTIONS(self):
            self._handle()

        def log_message(self, format, *args):
            pass

    return ThreadingHTTPServer((host, port), Handler)


class _BodyTooLarge(Exception):
    pass
