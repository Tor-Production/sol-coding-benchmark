"""JSON HTTP interface for the inventory store."""

import json
import re
import sqlite3
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from .store import Conflict, NotFound, Store


MAX_BODY_BYTES = 64 * 1024


class _RequestError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _decode_segment(segment):
    if re.search(r"%(?![0-9a-fA-F]{2})", segment):
        raise ValueError("Invalid URL encoding")
    return unquote(segment, encoding="utf-8", errors="strict")


def _reject_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


class _RequestHandler(BaseHTTPRequestHandler):
    # HTTP/1.0 closes each connection, including requests with unread bad bodies.
    protocol_version = "HTTP/1.0"
    timeout = 10

    def log_message(self, format, *args):
        pass

    def _send_json(self, status, record):
        body = json.dumps(record, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_error(self, code, message=None, explain=None):
        # Include errors produced by the standard-library request parser.
        if code == HTTPStatus.NOT_IMPLEMENTED:
            code, message = HTTPStatus.NOT_FOUND, "Unknown route"
        self.close_connection = True
        self._send_json(code, {"error": message or HTTPStatus(code).phrase})

    def _body_length(self):
        if self.headers.get("Transfer-Encoding") is not None:
            raise ValueError("Transfer-Encoding is not supported")
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) > 1:
            raise ValueError("Multiple Content-Length headers")
        if not lengths:
            return 0
        raw_length = lengths[0].strip()
        if not re.fullmatch(r"[0-9]+", raw_length):
            raise ValueError("Invalid Content-Length")
        # Compare before conversion, so even extremely long numeric headers
        # cannot exceed the body limit or Python's integer conversion limit.
        raw_length = raw_length.lstrip("0") or "0"
        limit = str(MAX_BODY_BYTES)
        if len(raw_length) > len(limit) or (
            len(raw_length) == len(limit) and raw_length > limit
        ):
            raise _RequestError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body exceeds 64 KiB")
        return int(raw_length)

    def _read_object(self, length):
        try:
            body = self.rfile.read(length)
        except OSError as error:
            raise ValueError("Could not read request body") from error
        if len(body) != length:
            raise ValueError("Incomplete request body")
        try:
            record = json.loads(body.decode("utf-8"), parse_constant=_reject_constant)
        except (ValueError, RecursionError) as error:
            raise ValueError("Malformed JSON") from error
        if not isinstance(record, dict):
            raise ValueError("Request body must be a JSON object")
        return record

    @staticmethod
    def _require_fields(record, *fields):
        for field in fields:
            if field not in record:
                raise ValueError(f"Missing field: {field}")

    def _dispatch(self):
        length = self._body_length()
        path = urlsplit(self.path).path
        if self.command == "GET":
            if path == "/health":
                return HTTPStatus.OK, {"ok": True}
            if path == "/report":
                return HTTPStatus.OK, self.store.report()
            if path.startswith("/items/"):
                segment = path[len("/items/"):]
                if segment and "/" not in segment:
                    return HTTPStatus.OK, self.store.get_item(_decode_segment(segment))
        elif self.command == "POST":
            if path == "/items":
                record = self._read_object(length)
                self._require_fields(record, "sku", "quantity")
                return HTTPStatus.CREATED, self.store.add_item(record["sku"], record["quantity"])
            if path == "/reservations":
                record = self._read_object(length)
                self._require_fields(record, "idempotency_key", "sku", "quantity")
                return HTTPStatus.CREATED, self.store.reserve(
                    record["idempotency_key"], record["sku"], record["quantity"]
                )
            match = re.fullmatch(r"/reservations/([^/]+)/release", path)
            if match:
                self._read_object(length)
                raw_id = _decode_segment(match.group(1))
                if not re.fullmatch(r"[0-9]+", raw_id):
                    raise ValueError("reservation_id must be a positive integer")
                return HTTPStatus.OK, self.store.release(int(raw_id))
        raise NotFound("Unknown route")

    def _handle_request(self):
        try:
            status, record = self._dispatch()
        except _RequestError as error:
            status, record = error.status, {"error": str(error)}
        except NotFound as error:
            status, record = HTTPStatus.NOT_FOUND, {"error": str(error)}
        except Conflict as error:
            status, record = HTTPStatus.CONFLICT, {"error": str(error)}
        except ValueError as error:
            status, record = HTTPStatus.BAD_REQUEST, {"error": str(error)}
        except (sqlite3.Error, OSError):
            status, record = HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Internal server error"}
        self._send_json(status, record)

    do_GET = _handle_request
    do_POST = _handle_request
    do_HEAD = _handle_request
    do_PUT = _handle_request
    do_PATCH = _handle_request
    do_DELETE = _handle_request
    do_OPTIONS = _handle_request


def create_server(db_path, host="127.0.0.1", port=0):
    """Bind a concurrent HTTP server; the caller controls its lifetime."""
    store = Store(db_path)

    class RequestHandler(_RequestHandler):
        pass

    RequestHandler.store = store
    return ThreadingHTTPServer((host, port), RequestHandler)
