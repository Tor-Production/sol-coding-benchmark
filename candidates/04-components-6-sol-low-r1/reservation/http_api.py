"""HTTP API."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit
from .store import Conflict, NotFound, Store

def create_server(db_path, host="127.0.0.1", port=0):
    store = Store(db_path)

    class Handler(BaseHTTPRequestHandler):
        def send_json(self, status, obj):
            data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def body(self):
            try:
                size = int(self.headers.get("Content-Length", ""))
            except ValueError:
                raise ValueError("invalid Content-Length") from None
            if size < 0:
                raise ValueError("invalid Content-Length")
            if size > 65536:
                raise OverflowError("request body too large")
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise ValueError("incomplete request body")
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                raise ValueError("invalid JSON") from None
            if not isinstance(data, dict):
                raise ValueError("JSON body must be an object")
            return data

        def handle_route(self):
            path = urlsplit(self.path).path
            try:
                if self.command == "GET":
                    if path == "/health":
                        return self.send_json(200, {"ok": True})
                    if path == "/report":
                        return self.send_json(200, store.report())
                    if path.startswith("/items/") and path.count("/") == 2:
                        return self.send_json(200, store.get_item(unquote(path[7:])))
                elif self.command == "POST":
                    if path == "/items":
                        data = self.body()
                        return self.send_json(201, store.add_item(data["sku"], data["quantity"]))
                    if path == "/reservations":
                        data = self.body()
                        return self.send_json(201, store.reserve(data["idempotency_key"], data["sku"], data["quantity"]))
                    parts = path.split("/")
                    if len(parts) == 4 and parts[1] == "reservations" and parts[3] == "release":
                        self.body()
                        try:
                            rid = int(parts[2])
                        except ValueError:
                            raise ValueError("invalid reservation_id") from None
                        return self.send_json(200, store.release(rid))
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
            self.handle_route()

        def do_POST(self):
            self.handle_route()

    return ThreadingHTTPServer((host, port), Handler)
