from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


STATIC_DIR = Path(__file__).resolve().parent / "static"


class DashboardHandler(BaseHTTPRequestHandler):
    app = None

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        elif parsed.path == "/style.css":
            self._send_file(STATIC_DIR / "style.css", "text/css; charset=utf-8")
        elif parsed.path == "/app.js":
            self._send_file(STATIC_DIR / "app.js", "application/javascript; charset=utf-8")
        elif parsed.path == "/api/status":
            self._send_json(self.app.status())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/display/on":
            self.app.set_display(True)
            self._send_json(self.app.status())
        elif parsed.path == "/api/display/off":
            self.app.set_display(False)
            self._send_json(self.app.status())
        elif parsed.path == "/api/display/mode":
            mode = self._read_mode(parsed)
            self.app.set_display_mode(mode)
            self._send_json(self.app.status())
        elif parsed.path == "/api/refresh":
            self.app.manual_refresh()
            self._send_json(self.app.status())
        else:
            self.send_response(404)
            self.end_headers()

    def _read_mode(self, parsed) -> str:
        query_mode = parse_qs(parsed.query).get("mode", [None])[0]
        if query_mode:
            return query_mode
        length = int(self.headers.get("Content-Length", "0") or 0)
        if not length:
            return "AUTO"
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8"))
            return str(payload.get("mode", "AUTO"))
        except json.JSONDecodeError:
            return "AUTO"

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            self.send_response(404)
            self.end_headers()
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args) -> None:
        pass


def run_server(app, host: str, port: int) -> None:
    DashboardHandler.app = app
    ThreadingHTTPServer((host, port), DashboardHandler).serve_forever()
