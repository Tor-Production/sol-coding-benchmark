"""Standard-library HTTP API."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit
from .store import Conflict, NotFound, Store

MAX_BODY = 64 * 1024

def create_server(db_path, host="127.0.0.1", port=0):
    store = Store(db_path)
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, value):
            body = json.dumps(value, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def _error(self, status, error): self._send(status, {"error": str(error)})
        def _body(self):
            raw = self.headers.get("Content-Length")
            try: length = int(raw) if raw is not None else 0
            except (TypeError, ValueError): raise ValueError("invalid Content-Length")
            if length < 0: raise ValueError("invalid Content-Length")
            if length > MAX_BODY: raise OverflowError("request body exceeds 64 KiB")
            try: value = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("malformed JSON") from exc
            if not isinstance(value, dict): raise ValueError("JSON body must be an object")
            return value
        @staticmethod
        def _fields(body, *names):
            try: return [body[name] for name in names]
            except KeyError as exc: raise ValueError(f"missing field: {exc.args[0]}") from exc
        def do_GET(self):
            path = urlsplit(self.path).path
            try:
                if path == "/health": self._send(200, {"ok": True})
                elif path == "/report": self._send(200, store.report())
                elif path.startswith("/items/") and path.count("/") == 2:
                    self._send(200, store.get_item(unquote(path[7:])))
                else: self._error(404, "not found")
            except NotFound as exc: self._error(404, exc)
            except (ValueError, TypeError) as exc: self._error(400, exc)
            except Exception as exc: self._error(500, exc)
        def do_POST(self):
            path = urlsplit(self.path).path
            try:
                is_release = path.startswith("/reservations/") and path.endswith("/release")
                if path not in ("/items", "/reservations") and not is_release:
                    self._error(404, "not found")
                    return
                body = self._body()
                if path == "/items":
                    sku, quantity = self._fields(body, "sku", "quantity")
                    self._send(201, store.add_item(sku, quantity))
                elif path == "/reservations":
                    key, sku, quantity = self._fields(body, "idempotency_key", "sku", "quantity")
                    self._send(201, store.reserve(key, sku, quantity))
                elif is_release:
                    parts = path.split("/")
                    if len(parts) != 4 or not parts[2]: self._error(404, "not found"); return
                    try: reservation_id = int(parts[2])
                    except ValueError as exc: raise ValueError("reservation id must be a positive integer") from exc
                    self._send(200, store.release(reservation_id))
            except OverflowError as exc: self._error(413, exc)
            except NotFound as exc: self._error(404, exc)
            except Conflict as exc: self._error(409, exc)
            except (ValueError, TypeError) as exc: self._error(400, exc)
            except Exception as exc: self._error(500, exc)
        def log_message(self, format, *args): pass
    return ThreadingHTTPServer((host, port), Handler)
