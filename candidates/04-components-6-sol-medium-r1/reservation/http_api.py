"""JSON HTTP interface for the reservation store."""

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from .store import Conflict, NotFound, Store


_MAX_BODY = 64 * 1024
_RELEASE_PATH = re.compile(r"/reservations/([^/]+)/release\Z")


def create_server(db_path, host="127.0.0.1", port=0):
    store = Store(db_path)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, payload):
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            length_header = self.headers.get("Content-Length")
            try:
                length = int(length_header)
            except (TypeError, ValueError):
                raise ValueError("invalid Content-Length") from None
            if length < 0:
                raise ValueError("invalid Content-Length")
            if length > _MAX_BODY:
                raise _BodyTooLarge("request body exceeds 64 KiB")
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("incomplete request body")
            try:
                body = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ValueError("malformed JSON") from None
            if not isinstance(body, dict):
                raise ValueError("JSON body must be an object")
            return body

        def _dispatch(self, method):
            path = urlsplit(self.path).path
            if method == "GET":
                if path == "/health":
                    return 200, {"ok": True}
                if path == "/report":
                    return 200, store.report()
                if path.startswith("/items/") and path != "/items/" and "/" not in path[len("/items/"):]:
                    sku = unquote(path[len("/items/"):], encoding="utf-8", errors="strict")
                    return 200, store.get_item(sku)
            elif method == "POST":
                if path == "/items":
                    body = self._body()
                    return 201, store.add_item(body["sku"], body["quantity"])
                if path == "/reservations":
                    body = self._body()
                    return 201, store.reserve(
                        body["idempotency_key"], body["sku"], body["quantity"]
                    )
                match = _RELEASE_PATH.fullmatch(path)
                if match:
                    self._body()
                    try:
                        reservation_id = int(match.group(1))
                    except ValueError:
                        raise ValueError("reservation_id must be a positive integer") from None
                    return 200, store.release(reservation_id)
            raise NotFound("route not found")

        def _handle(self, method):
            try:
                status, payload = self._dispatch(method)
            except _BodyTooLarge as exc:
                status, payload = 413, {"error": str(exc)}
            except (ValueError, KeyError, UnicodeDecodeError) as exc:
                status, payload = 400, {"error": str(exc)}
            except NotFound as exc:
                status, payload = 404, {"error": str(exc)}
            except Conflict as exc:
                status, payload = 409, {"error": str(exc)}
            except Exception:
                status, payload = 500, {"error": "internal server error"}
            self._send(status, payload)

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

    return ThreadingHTTPServer((host, port), Handler)


class _BodyTooLarge(Exception):
    pass
