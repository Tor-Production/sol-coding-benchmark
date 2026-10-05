"""Verify workspace writes and local HTTP through the candidate sandbox."""
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import sqlite3
import sys
import threading

marker = Path("native-probe-marker.txt")
marker.write_text("sandbox write succeeded", encoding="utf-8")
assert marker.read_text(encoding="utf-8") == "sandbox write succeeded"
with sqlite3.connect("native-probe.db") as connection:
    connection.execute("CREATE TABLE IF NOT EXISTS probe(value INTEGER)")
    connection.execute("DELETE FROM probe")
    connection.execute("INSERT INTO probe VALUES (42)")
    assert connection.execute("SELECT value FROM probe").fetchone()[0] == 42


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


server = HTTPServer(("127.0.0.1", 0), Handler)
thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
thread.start()
try:
    client = http.client.HTTPConnection(*server.server_address, timeout=5)
    client.request("GET", "/")
    response = client.getresponse()
    assert response.status == 200 and json.loads(response.read()) == {"ok": True}
    client.close()
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)
print(json.dumps({"workspace_write": True, "sqlite": True, "loopback_http": True,
                  "python": sys.version.split()[0]}))
