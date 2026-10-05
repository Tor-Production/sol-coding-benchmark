"""JSON HTTP API."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit
from .store import Store, Conflict, NotFound

def create_server(db_path, host="127.0.0.1", port=0):
    store = Store(db_path)

    class Handler(BaseHTTPRequestHandler):
        def send_json(self, status, value):
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def body(self):
            try:
                size = int(self.headers["Content-Length"])
            except (KeyError, ValueError):
                raise ValueError("invalid Content-Length") from None
            if size < 0:
                raise ValueError("invalid Content-Length")
            if size > 65536:
                raise OverflowError("request body too large")
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise ValueError("incomplete request body")
            try:
                value = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                raise ValueError("invalid JSON") from None
            if not isinstance(value, dict):
                raise ValueError("JSON object required")
            return value

        def dispatch(self):
            path = urlsplit(self.path).path
            try:
                if self.command == "GET" and path == "/health":
                    return self.send_json(200, {"ok": True})
                if self.command == "GET" and path == "/report":
                    return self.send_json(200, store.report())
                if self.command == "GET" and path.startswith("/items/"):
                    return self.send_json(200, store.get_item(unquote(path[7:])))
                if self.command == "POST" and path == "/items":
                    data = self.body()
                    return self.send_json(201, store.add_item(data["sku"], data["quantity"]))
                if self.command == "POST" and path == "/reservations":
                    data = self.body()
                    return self.send_json(201, store.reserve(data["idempotency_key"], data["sku"], data["quantity"]))
                parts = path.split("/")
                if self.command == "POST" and len(parts) == 4 and parts[1] == "reservations" and parts[3] == "release":
                    self.body()
                    try:
                        ident = int(parts[2])
                    except ValueError:
                        raise ValueError("invalid reservation_id") from None
                    return self.send_json(200, store.release(ident))
                self.send_json(404, {"error": "route not found"})
            except KeyError as exc:
                self.send_json(400, {"error": f"missing field: {exc.args[0]}"})
            except OverflowError as exc:
                self.send_json(413, {"error": str(exc)})
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
            except NotFound as exc:
                self.send_json(404, {"error": str(exc)})
            except Conflict as exc:
                self.send_json(409, {"error": str(exc)})

        def do_GET(self):
            self.dispatch()

        def do_POST(self):
            self.dispatch()

        def log_message(self, format, *args):
            pass

    return ThreadingHTTPServer((host, port), Handler)
