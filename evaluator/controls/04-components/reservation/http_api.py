import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit
from .store import Store, Conflict, NotFound


def create_server(db_path, host="127.0.0.1", port=0):
    store = Store(db_path)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, data):
            payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            self.handle_route(False)

        def do_POST(self):
            self.handle_route(True)

        def handle_route(self, post):
            try:
                path = urlsplit(self.path).path
                data = {}
                if post:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length > 65536:
                        return self.reply(413, {"error": "Request too large"})
                    if length < 0:
                        raise ValueError("Invalid length")
                    data = json.loads(self.rfile.read(length))
                    if not isinstance(data, dict):
                        raise ValueError("Expected an object")
                if not post and path == "/health":
                    return self.reply(200, {"ok": True})
                if not post and path == "/report":
                    return self.reply(200, store.report())
                if not post and path.startswith("/items/"):
                    return self.reply(200, store.get_item(unquote(path[7:])))
                if post and path == "/items":
                    return self.reply(201, store.add_item(data["sku"], data["quantity"]))
                if post and path == "/reservations":
                    return self.reply(201, store.reserve(data["idempotency_key"], data["sku"], data["quantity"]))
                parts = path.split("/")
                if post and len(parts) == 4 and parts[1] == "reservations" and parts[3] == "release":
                    return self.reply(200, store.release(int(parts[2])))
                self.reply(404, {"error": "Route not found"})
            except NotFound as exc:
                self.reply(404, {"error": str(exc)})
            except Conflict as exc:
                self.reply(409, {"error": str(exc)})
            except (ValueError, TypeError, KeyError) as exc:
                self.reply(400, {"error": str(exc)})

    return ThreadingHTTPServer((host, port), Handler)
