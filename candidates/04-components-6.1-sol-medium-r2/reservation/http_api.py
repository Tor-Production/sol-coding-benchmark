"""Standard-library JSON HTTP interface."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import socket
import time
from urllib.parse import unquote, urlsplit

from .store import Conflict, NotFound, Store


class _BodyTooLarge(Exception):
    pass


def create_server(db_path, host="127.0.0.1", port=0):
    store = Store(db_path)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def _respond(self, status, record):
            body = json.dumps(record, ensure_ascii=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            if self.headers.get("Transfer-Encoding"):
                raise ValueError("Transfer-Encoding is not supported")
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or not re.fullmatch(r"[0-9]+", lengths[0].strip()):
                raise ValueError("A valid Content-Length is required")
            length = int(lengths[0])
            if length > 64 * 1024:
                raise _BodyTooLarge("Request body exceeds 64 KiB")
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
                raise ValueError("Malformed JSON") from exc
            if not isinstance(body, dict):
                raise ValueError("JSON body must be an object")
            return body

        def _finish_oversized(self):
            # Send the response before draining, so header-only clients also
            # receive 413 promptly. A bounded drain avoids Windows resetting
            # the socket while the client is still sending its request body.
            self.wfile.flush()
            self.connection.shutdown(socket.SHUT_WR)
            deadline = time.monotonic() + 0.2
            remaining = int(self.headers["Content-Length"])
            try:
                while remaining and time.monotonic() < deadline:
                    self.connection.settimeout(max(0.001, deadline - time.monotonic()))
                    chunk = self.rfile.read1(min(8192, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
            except OSError:
                pass

        def _dispatch(self):
            try:
                declared = self.headers.get("Content-Length")
                if declared is not None:
                    if not re.fullmatch(r"[0-9]+", declared.strip()):
                        raise ValueError("Invalid Content-Length")
                    if int(declared) > 64 * 1024:
                        raise _BodyTooLarge("Request body exceeds 64 KiB")
                path = urlsplit(self.path).path
                if self.command == "GET":
                    if path == "/health":
                        return self._respond(200, {"ok": True})
                    if path == "/report":
                        return self._respond(200, store.report())
                    if path.startswith("/items/") and "/" not in path[len("/items/"):]:
                        return self._respond(200, store.get_item(unquote(path[len("/items/"):], errors="strict")))
                elif self.command == "POST":
                    release = re.fullmatch(r"/reservations/([^/]+)/release", path)
                    if path in ("/items", "/reservations") or release:
                        body = self._body()
                        if path == "/items":
                            return self._respond(201, store.add_item(body["sku"], body["quantity"]))
                        if path == "/reservations":
                            return self._respond(201, store.reserve(body["idempotency_key"], body["sku"], body["quantity"]))
                        identifier = release.group(1)
                        if not re.fullmatch(r"[0-9]+", identifier):
                            raise ValueError("reservation_id must be a positive integer")
                        return self._respond(200, store.release(int(identifier)))
                raise NotFound("Route not found")
            except _BodyTooLarge as exc:
                self._respond(413, {"error": str(exc)})
                self._finish_oversized()
            except NotFound as exc:
                self._respond(404, {"error": str(exc)})
            except Conflict as exc:
                self._respond(409, {"error": str(exc)})
            except KeyError as exc:
                self._respond(400, {"error": f"Missing field: {exc.args[0]}"})
            except (ValueError, OverflowError) as exc:
                self._respond(400, {"error": str(exc)})

        do_GET = _dispatch
        do_POST = _dispatch
        do_PUT = _dispatch
        do_PATCH = _dispatch
        do_DELETE = _dispatch
        do_HEAD = _dispatch
        do_OPTIONS = _dispatch
        do_TRACE = _dispatch
        do_CONNECT = _dispatch

    return ThreadingHTTPServer((host, port), Handler)
