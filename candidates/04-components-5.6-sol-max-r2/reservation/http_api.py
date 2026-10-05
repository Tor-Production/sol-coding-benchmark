"""Standard-library JSON HTTP API for the reservation service."""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

from .store import Conflict, NotFound, Store


_MAX_BODY_BYTES = 64 * 1024
_RELEASE_PATH = re.compile(r"/reservations/([^/]+)/release\Z")


class _ReservationHTTPServer(ThreadingHTTPServer):
    daemon_threads = True


class _Handler(BaseHTTPRequestHandler):
    server: _ReservationHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        # A library-created server should not write request logs to its
        # caller's stderr by default.
        return

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(encoded)

    def _handle(self, operation: Callable[[], tuple[int, dict[str, Any]]]) -> None:
        try:
            status, payload = operation()
        except NotFound as error:
            status, payload = 404, {"error": str(error)}
        except Conflict as error:
            status, payload = 409, {"error": str(error)}
        except (ValueError, KeyError, UnicodeError) as error:
            status, payload = 400, {"error": str(error) or "invalid request"}
        except Exception:
            # A bad request or unexpected storage failure must not terminate
            # the serving loop (or expose an implementation traceback).
            status, payload = 500, {"error": "internal server error"}

        try:
            self._send_json(status, payload)
        except (BrokenPipeError, ConnectionError):
            pass

    def _path(self) -> str:
        return urlsplit(self.path).path

    def _content_length(self) -> int:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValueError("Content-Length is required")
        try:
            length = int(raw_length, 10)
        except (TypeError, ValueError) as error:
            raise ValueError("invalid Content-Length") from error
        if length < 0:
            raise ValueError("invalid Content-Length")
        if length > _MAX_BODY_BYTES:
            raise _PayloadTooLarge
        return length

    def _read_json_object(self, length: int) -> dict[str, Any]:
        body = self.rfile.read(length)
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("malformed JSON") from error
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    @staticmethod
    def _field(body: dict[str, Any], name: str) -> Any:
        if name not in body:
            raise ValueError(f"missing field: {name}")
        return body[name]

    def _get(self) -> tuple[int, dict[str, Any]]:
        path = self._path()
        if path == "/health":
            return 200, {"ok": True}
        if path == "/report":
            return 200, self.server.store.report()
        if path.startswith("/items/"):
            encoded_sku = path[len("/items/") :]
            if encoded_sku and "/" not in encoded_sku:
                sku = unquote(encoded_sku, encoding="utf-8", errors="strict")
                return 200, self.server.store.get_item(sku)
        return 404, {"error": "not found"}

    def _post(self) -> tuple[int, dict[str, Any]]:
        path = self._path()
        try:
            length = self._content_length()
        except _PayloadTooLarge:
            self.close_connection = True
            return 413, {"error": "request body too large"}

        if path == "/items":
            body = self._read_json_object(length)
            record = self.server.store.add_item(
                self._field(body, "sku"), self._field(body, "quantity")
            )
            return 201, record

        if path == "/reservations":
            body = self._read_json_object(length)
            record = self.server.store.reserve(
                self._field(body, "idempotency_key"),
                self._field(body, "sku"),
                self._field(body, "quantity"),
            )
            return 201, record

        match = _RELEASE_PATH.fullmatch(path)
        if match is not None:
            # Release still requires a valid JSON request object, even though
            # it has no fields.
            self._read_json_object(length)
            encoded_id = match.group(1)
            if re.fullmatch(r"[0-9]+", encoded_id) is None:
                raise ValueError("reservation id must be a positive integer")
            reservation_id = int(encoded_id, 10)
            return 200, self.server.store.release(reservation_id)

        return 404, {"error": "not found"}

    def do_GET(self) -> None:
        self._handle(self._get)

    def do_POST(self) -> None:
        self._handle(self._post)

    def _unknown_method(self) -> None:
        self._handle(lambda: (404, {"error": "not found"}))

    do_DELETE = _unknown_method
    do_HEAD = _unknown_method
    do_OPTIONS = _unknown_method
    do_PATCH = _unknown_method
    do_PUT = _unknown_method


class _PayloadTooLarge(Exception):
    pass


def create_server(
    db_path: str, host: str = "127.0.0.1", port: int = 0
) -> ThreadingHTTPServer:
    """Create and bind a threaded reservation HTTP server."""

    store = Store(db_path)
    server = _ReservationHTTPServer((host, port), _Handler)
    server.store = store
    return server
