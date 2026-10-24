"""HTTP server: static UI plus a small JSON API.

  GET  /api/health   liveness probe
  GET  /api/state    dashboard data
  GET  /api/config   services for the settings page (secrets masked)
  POST /api/config   save services edited in the settings page

With a host agent (PIHUB_AGENT_SOCKET, see hostagent.py):
  GET  /api/host          agent status and running Claude sessions
  GET  /api/host/dirs     folders to start a session in (?path=)
  POST /api/host/claude   start a session ({"path", "name"})
  POST /api/host/stop     stop a session ({"id"})
  POST /api/host/reboot   restart the machine

Settings and host endpoints can be protected with PIHUB_PASSWORD (sent by the
UI as a Bearer token); PIHUB_READONLY=1 disables every POST.
"""

import hmac
import json
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol

from .config import ConfigError
from .hostagent import AgentClient, AgentError

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
# POST path -> (agent action, body fields passed through)
HOST_ACTIONS = {
    "/api/host/claude": ("start", ("path", "name")),
    "/api/host/stop": ("stop", ("id",)),
    "/api/host/reboot": ("reboot", ()),
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


def make_handler(
    hub: HubAPI, password: str = "", readonly: bool = False, agent: AgentClient | None = None
) -> type[BaseHTTPRequestHandler]:
    """Builds the request handler class bound to one hub and its access settings."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "pi-hub"
        sys_version = ""

        def log_message(self, format: str, *args: Any) -> None:
            """Silences the default per-request stderr log."""
            pass

        def send(self, code: int, body: object, ctype: str = "application/json", cache: str = "no-store") -> None:
            """Sends a response with the security headers; ``body`` is bytes or JSON-able."""
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

        def authorised(self) -> bool:
            """True if no password is set or the Bearer token matches (constant-time)."""
            if not password:
                return True
            supplied = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            return hmac.compare_digest(supplied.encode(), password.encode())

        def do_HEAD(self) -> None:
            self.do_GET()

        def do_GET(self) -> None:
            url = urllib.parse.urlparse(self.path)
            path = url.path
            if path == "/api/health":
                return self.send(200, {"ok": True})
            if path == "/api/state":
                state = {**hub.state(), "readonly": readonly, "locked": bool(password), "hostAgent": bool(agent)}
                return self.send(200, state)
            if path == "/api/config":
                if not self.authorised():
                    return self.send(401, {"error": "Password required"})
                return self.send(200, {**hub.settings(), "readonly": readonly})
            if path in ("/api/host", "/api/host/dirs"):
                if not agent:
                    return self.send(404, {"error": "No host agent configured"})
                if not self.authorised():
                    return self.send(401, {"error": "Password required"})
                if path == "/api/host":
                    return self.host_call("status")
                rel = urllib.parse.parse_qs(url.query).get("path", [""])[0]
                return self.host_call("list", path=rel)
            return self.static(path)

        def do_POST(self) -> None:
            path = urllib.parse.urlparse(self.path).path
            host_action = HOST_ACTIONS.get(path)
            if path != "/api/config" and not (host_action and agent):
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
                body = json.loads(self.rfile.read(length) or b"{}")
                if host_action:
                    if not isinstance(body, dict):
                        raise ValueError("Expected a JSON object")
                    params = {k: str(body.get(k, "")) for k in host_action[1]}
                    return self.host_call(host_action[0], **params)
                notices = hub.update_services(body)
                return self.send(200, {**hub.settings(), "readonly": readonly, "notices": notices})
            except (ConfigError, ValueError) as e:
                return self.send(400, {"error": str(e)})

        def host_call(self, action: str, **params: str) -> None:
            """Forwards one request to the host agent; its refusals come back as 400s."""
            if agent is None:
                return self.send(404, {"error": "No host agent configured"})
            try:
                return self.send(200, agent.call(action, **params))
            except AgentError as e:
                code = 502 if str(e).startswith("Host agent") else 400
                return self.send(code, {"error": str(e)})

        def static(self, path: str) -> None:
            """Serves a file from STATIC_DIR, refusing anything that resolves outside it."""
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


def serve(
    hub: HubAPI,
    host: str = "0.0.0.0",
    port: int = 8000,
    password: str = "",
    readonly: bool = False,
    agent: AgentClient | None = None,
) -> ThreadingHTTPServer:
    """Creates (but does not start) the HTTP server; call ``serve_forever`` on it."""
    httpd = ThreadingHTTPServer((host, port), make_handler(hub, password, readonly, agent))
    httpd.daemon_threads = True
    return httpd
