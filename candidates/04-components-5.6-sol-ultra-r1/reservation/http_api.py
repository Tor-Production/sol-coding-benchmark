"""Standard-library HTTP API for the reservation service."""

from __future__ import annotations

import json
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from .store import Conflict, NotFound, Store


_MAX_BODY_BYTES = 64 * 1024
_RELEASE_PATH = re.compile(r"/reservations/([^/]+)/release\Z")


class _RequestError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def create_server(db_path, host="127.0.0.1", port=0):
    """Create and bind a threaded HTTP server without starting its loop."""

    store = Store(db_path)

    class ReservationHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send_json(self, status: int, value: object, *, body: bool = True) -> None:
            encoded = json.dumps(
                value, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            if body:
                self.wfile.write(encoded)

        def _request_path(self) -> str:
            try:
                return urlsplit(self.path).path
            except ValueError as exc:
                raise _RequestError(HTTPStatus.BAD_REQUEST, "invalid request path") from exc

        def _read_json_object(self) -> dict[str, object]:
            if self.headers.get("Transfer-Encoding") is not None:
                self.close_connection = True
                raise _RequestError(
                    HTTPStatus.BAD_REQUEST, "chunked request bodies are not supported"
                )

            lengths = self.headers.get_all("Content-Length", failobj=[])
            if len(lengths) != 1:
                self.close_connection = True
                raise _RequestError(
                    HTTPStatus.BAD_REQUEST, "exactly one Content-Length is required"
                )
            raw_length = lengths[0]
            if not raw_length or not raw_length.isascii() or not raw_length.isdigit():
                self.close_connection = True
                raise _RequestError(HTTPStatus.BAD_REQUEST, "invalid Content-Length")

            normalized_length = raw_length.lstrip("0") or "0"
            maximum = str(_MAX_BODY_BYTES)
            if len(normalized_length) > len(maximum) or (
                len(normalized_length) == len(maximum)
                and normalized_length > maximum
            ):
                self.close_connection = True
                raise _RequestError(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    "request body exceeds 65536 bytes",
                )
            length = int(normalized_length)

            raw_body = self.rfile.read(length)
            if len(raw_body) != length:
                self.close_connection = True
                raise _RequestError(HTTPStatus.BAD_REQUEST, "incomplete request body")
            try:
                text = raw_body.decode("utf-8")
                value = json.loads(text, parse_constant=_reject_json_constant)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                raise _RequestError(HTTPStatus.BAD_REQUEST, "malformed JSON") from exc
            if not isinstance(value, dict):
                raise _RequestError(
                    HTTPStatus.BAD_REQUEST, "JSON request body must be an object"
                )
            return value

        @staticmethod
        def _required(value: dict[str, object], field: str) -> object:
            if field not in value:
                raise ValueError(f"missing field: {field}")
            return value[field]

        def _run(self, operation) -> None:
            try:
                operation()
            except _RequestError as exc:
                self._send_json(exc.status, {"error": str(exc)})
            except ValueError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except NotFound as exc:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
            except Conflict as exc:
                self._send_json(HTTPStatus.CONFLICT, {"error": str(exc)})
            except Exception:
                # Application failures remain isolated to this request and
                # always retain the API's JSON response format.
                self.close_connection = True
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": "internal server error"},
                )

        def do_GET(self) -> None:
            self._run(self._do_GET)

        def _do_GET(self) -> None:
            path = self._request_path()
            if path == "/health":
                self._send_json(HTTPStatus.OK, {"ok": True})
                return
            if path == "/report":
                self._send_json(HTTPStatus.OK, store.report())
                return
            if path.startswith("/items/"):
                raw_sku = path[len("/items/") :]
                try:
                    sku = unquote(raw_sku, encoding="utf-8", errors="strict")
                except UnicodeDecodeError as exc:
                    raise ValueError("invalid URL-encoded sku") from exc
                self._send_json(HTTPStatus.OK, store.get_item(sku))
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "route not found"})

        def do_POST(self) -> None:
            self._run(self._do_POST)

        def _do_POST(self) -> None:
            path = self._request_path()
            if path == "/items":
                body = self._read_json_object()
                result = store.add_item(
                    self._required(body, "sku"),
                    self._required(body, "quantity"),
                )
                self._send_json(HTTPStatus.CREATED, result)
                return
            if path == "/reservations":
                body = self._read_json_object()
                result = store.reserve(
                    self._required(body, "idempotency_key"),
                    self._required(body, "sku"),
                    self._required(body, "quantity"),
                )
                self._send_json(HTTPStatus.CREATED, result)
                return

            match = _RELEASE_PATH.fullmatch(path)
            if match is not None:
                self._read_json_object()
                raw_id = match.group(1)
                if not raw_id.isascii() or not raw_id.isdigit():
                    raise ValueError("reservation id must be a positive integer")
                result = store.release(int(raw_id))
                self._send_json(HTTPStatus.OK, result)
                return
            # The body of an unknown POST route has not been consumed. Closing
            # prevents it from being parsed as a second request on HTTP/1.1.
            self.close_connection = True
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "route not found"})

        def do_HEAD(self) -> None:
            self._send_json(
                HTTPStatus.NOT_FOUND, {"error": "route not found"}, body=False
            )

        def _unsupported_method(self) -> None:
            self.close_connection = True
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "route not found"})

        do_DELETE = _unsupported_method
        do_PATCH = _unsupported_method
        do_PUT = _unsupported_method
        do_OPTIONS = _unsupported_method

        def log_message(self, format: str, *args: object) -> None:
            # A reusable library server should not write request logs to the
            # embedding application's stderr unless it opts in itself.
            return

    class ReservationHTTPServer(ThreadingHTTPServer):
        daemon_threads = True

    return ReservationHTTPServer((host, port), ReservationHandler)
