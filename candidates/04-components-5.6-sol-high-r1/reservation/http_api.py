"""Standard-library JSON HTTP API for the reservation store."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlsplit

from .store import Conflict, NotFound, Store


_MAX_BODY_BYTES = 64 * 1024


class _PayloadTooLarge(Exception):
    pass


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def _required(body: dict[str, Any], field: str) -> Any:
    if field not in body:
        raise ValueError(f"missing field: {field}")
    return body[field]


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def log_message(self, format: str, *args: Any) -> None:
        # A library-created server should not write request logs to the host
        # application's stderr unless it explicitly adds its own logging.
        return

    def _write_json(self, status: int, value: dict[str, Any]) -> None:
        encoded = json.dumps(
            value, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _content_length(self, required: bool = False) -> int | None:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            if required:
                raise ValueError("missing Content-Length")
            return None
        try:
            length = int(raw_length, 10)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid Content-Length") from exc
        if length < 0:
            raise ValueError("invalid Content-Length")
        if length > _MAX_BODY_BYTES:
            self.close_connection = True
            raise _PayloadTooLarge("request body exceeds 64 KiB")
        return length

    def _json_body(self) -> dict[str, Any]:
        length = self._content_length(required=True)
        assert length is not None
        data = self.rfile.read(length)
        if len(data) != length:
            raise ValueError("incomplete request body")
        try:
            body = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("malformed JSON") from exc
        if not isinstance(body, dict):
            raise ValueError("JSON body must be an object")
        return body

    def _dispatch_get(self, path: str) -> tuple[int, dict[str, Any]]:
        if path == "/health":
            return 200, {"ok": True}
        if path == "/report":
            return 200, self.server.store.report()
        if path.startswith("/items/"):
            encoded_sku = path[len("/items/") :]
            try:
                sku = unquote(encoded_sku, encoding="utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise ValueError("invalid URL-encoded sku") from exc
            return 200, self.server.store.get_item(sku)
        raise NotFound("route not found")

    def _dispatch_post(self, path: str) -> tuple[int, dict[str, Any]]:
        if path == "/items":
            body = self._json_body()
            return 201, self.server.store.add_item(
                _required(body, "sku"), _required(body, "quantity")
            )

        if path == "/reservations":
            body = self._json_body()
            return 201, self.server.store.reserve(
                _required(body, "idempotency_key"),
                _required(body, "sku"),
                _required(body, "quantity"),
            )

        prefix = "/reservations/"
        suffix = "/release"
        if path.startswith(prefix) and path.endswith(suffix):
            encoded_id = path[len(prefix) : -len(suffix)]
            if not encoded_id or "/" in encoded_id:
                raise NotFound("route not found")
            body = self._json_body()
            # Parsing the required request object before the ID ensures a bad
            # body is consistently a request-validation error.
            del body
            try:
                reservation_id = int(encoded_id, 10)
            except ValueError as exc:
                raise ValueError("reservation id must be a positive integer") from exc
            return 200, self.server.store.release(reservation_id)

        raise NotFound("route not found")

    def _handle(self, method: str) -> None:
        try:
            # Enforce the body bound before route dispatch so a request cannot
            # bypass it by targeting an unknown endpoint.
            self._content_length()
            path = urlsplit(self.path).path
            if method == "GET":
                status, response = self._dispatch_get(path)
            elif method == "POST":
                status, response = self._dispatch_post(path)
            else:
                raise NotFound("route not found")
        except _PayloadTooLarge as exc:
            status, response = 413, {"error": str(exc)}
        except Conflict as exc:
            status, response = 409, {"error": str(exc)}
        except NotFound as exc:
            status, response = 404, {"error": str(exc)}
        except (ValueError, TypeError) as exc:
            status, response = 400, {"error": str(exc)}
        except Exception:
            # Keep an unexpected operation failure local to this request and
            # avoid exposing database or filesystem details to clients.
            status, response = 500, {"error": "internal server error"}
        self._write_json(status, response)

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PUT(self) -> None:
        self._handle("PUT")

    def do_PATCH(self) -> None:
        self._handle("PATCH")

    def do_DELETE(self) -> None:
        self._handle("DELETE")

    def do_HEAD(self) -> None:
        self._handle("HEAD")

    def do_OPTIONS(self) -> None:
        self._handle("OPTIONS")


def create_server(
    db_path: str, host: str = "127.0.0.1", port: int = 0
) -> ThreadingHTTPServer:
    """Create and bind a threaded HTTP server without starting its loop."""

    store = Store(db_path)
    server = _Server((host, port), _Handler)
    server.store = store
    return server
