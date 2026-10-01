"""HTTP server: static UI plus a small JSON API.

  GET  /api/health   liveness probe
  GET  /api/state    dashboard data
  GET  /api/config   services for the settings page (secrets masked)
  POST /api/config   save services edited in the settings page

Settings endpoints can be protected with PIHUB_PASSWORD (sent by the UI as a
Bearer token) or disabled with PIHUB_READONLY=1.
"""

import hmac
import json
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Protocol

from .config import ConfigError

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
MAX_BODY = 200_000
CONTENT_TYPES = {
    "html": "text/html; charset=utf-8",
    "js": "text/javascript; charset=utf-8",
    "css": "text/css; charset=utf-8",
    "svg": "image/svg+xml",
    "png": "image/png",
    "woff2": "font/woff2",
    "txt": "text/plain; charset=utf-8",
}
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    ),
}


class HubAPI(Protocol):
    """What the HTTP layer needs from a hub. ``hub.Hub`` and ``demo.DemoHub`` both fit."""

    def state(self) -> dict: ...

    def settings(self) -> dict: ...

    def update_services(self, payload: dict) -> list[str]: ...


def make_handler(hub: HubAPI, password="", readonly=False):
    class Handler(BaseHTTPRequestHandler):
        server_version = "pi-hub"
        sys_version = ""

        def log_message(self, *args):
            pass

        def send(self, code, body, ctype="application/json", cache="no-store"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", cache)
            for k, v in SECURITY_HEADERS.items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def authorised(self):
            if not password:
                return True
            supplied = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            return hmac.compare_digest(supplied.encode(), password.encode())

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if path == "/api/health":
                return self.send(200, {"ok": True})
            if path == "/api/state":
                return self.send(200, {**hub.state(), "readonly": readonly, "locked": bool(password)})
            if path == "/api/config":
                if not self.authorised():
                    return self.send(401, {"error": "Password required"})
                return self.send(200, {**hub.settings(), "readonly": readonly})
            return self.static(path)

        def do_POST(self):
            if urllib.parse.urlparse(self.path).path != "/api/config":
                return self.send(404, {"error": "Not found"})
            if readonly:
                return self.send(403, {"error": "Settings are read-only (PIHUB_READONLY is set)"})
            if not self.authorised():
                return self.send(401, {"error": "Password required"})
            # JSON only: stops plain cross-site form posts.
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                return self.send(415, {"error": "Content-Type must be application/json"})
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length > MAX_BODY:
                    return self.send(413, {"error": "Request too large"})
                notices = hub.update_services(json.loads(self.rfile.read(length)))
                return self.send(200, {**hub.settings(), "readonly": readonly, "notices": notices})
            except (ConfigError, ValueError) as e:
                return self.send(400, {"error": str(e)})

        def static(self, path):
            name = "index.html" if path in ("/", "") else path.lstrip("/")
            root = os.path.realpath(STATIC_DIR)
            full = os.path.realpath(os.path.join(root, name))
            if not full.startswith(root + os.sep) or not os.path.isfile(full):
                return self.send(404, {"error": "Not found"})
            ext = full.rsplit(".", 1)[-1]
            cache = "public, max-age=604800, immutable" if ext == "woff2" else "no-cache"
            with open(full, "rb") as f:
                self.send(200, f.read(), CONTENT_TYPES.get(ext, "application/octet-stream"), cache)

    return Handler


def serve(hub: HubAPI, host="0.0.0.0", port=8000, password="", readonly=False):
    httpd = ThreadingHTTPServer((host, port), make_handler(hub, password, readonly))
    httpd.daemon_threads = True
    return httpd
