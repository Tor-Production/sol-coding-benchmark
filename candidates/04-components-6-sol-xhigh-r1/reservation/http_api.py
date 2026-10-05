"""JSON HTTP interface for the reservation store."""

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from .store import Conflict, NotFound, Store


MAX_BODY_BYTES = 64 * 1024


def create_server(db_path, host="127.0.0.1", port=0):
    store = Store(db_path)

    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, status, record):
            body = json.dumps(record, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _content_length(self):
            header = self.headers.get("Content-Length")
            try:
                length = int(header) if header is not None else 0
            except ValueError as exc:
                raise ValueError("invalid Content-Length") from exc
            if length < 0:
                raise ValueError("invalid Content-Length")
            if length > MAX_BODY_BYTES:
                raise OverflowError("request body exceeds 64 KiB")
            return length

        def _request_object(self):
            length = self._content_length()
            try:
                data = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("malformed JSON") from exc
            if not isinstance(data, dict):
                raise ValueError("JSON body must be an object")
            return data

        def _dispatch(self):
            path = urlsplit(self.path).path
            if self.command == "GET":
                if path == "/health":
                    return HTTPStatus.OK, {"ok": True}
                if path == "/report":
                    return HTTPStatus.OK, store.report()
                if path.startswith("/items/"):
                    encoded_sku = path[len("/items/"):]
                    if encoded_sku and "/" not in encoded_sku:
                        return HTTPStatus.OK, store.get_item(unquote(encoded_sku))
            elif self.command == "POST":
                if path == "/items":
                    data = self._request_object()
                    return HTTPStatus.CREATED, store.add_item(
                        data["sku"], data["quantity"]
                    )
                if path == "/reservations":
                    data = self._request_object()
                    return HTTPStatus.CREATED, store.reserve(
                        data["idempotency_key"], data["sku"], data["quantity"]
                    )
                parts = path.split("/")
                if (len(parts) == 4 and parts[1] == "reservations"
                        and parts[3] == "release" and parts[2]):
                    self._request_object()
                    try:
                        reservation_id = int(parts[2])
                    except ValueError as exc:
                        raise ValueError("reservation_id must be a positive integer") from exc
                    return HTTPStatus.OK, store.release(reservation_id)
            raise NotFound("route not found")

        def _handle(self):
            try:
                self._content_length()
                status, record = self._dispatch()
            except OverflowError as exc:
                self.close_connection = True
                status, record = HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": str(exc)}
            except (ValueError, KeyError) as exc:
                status, record = HTTPStatus.BAD_REQUEST, {"error": str(exc)}
            except NotFound as exc:
                status, record = HTTPStatus.NOT_FOUND, {"error": str(exc)}
            except Conflict as exc:
                status, record = HTTPStatus.CONFLICT, {"error": str(exc)}
            except Exception:
                status, record = HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal server error"}
            self._send_json(status, record)

        def do_GET(self):
            self._handle()

        def do_POST(self):
            self._handle()

        def send_error(self, code, message=None, explain=None):
            # BaseHTTPRequestHandler otherwise sends HTML for unsupported methods.
            if code == HTTPStatus.NOT_IMPLEMENTED:
                code = HTTPStatus.NOT_FOUND
            self._send_json(code, {"error": message or HTTPStatus(code).phrase})

    return ThreadingHTTPServer((host, port), Handler)
