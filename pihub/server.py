"""HTTP server: static UI plus a small JSON API.

  GET  /api/health   liveness probe
  GET  /api/auth     whether sign-in is on and who is signed in
  POST /api/login    sign in ({"username", "password"}); sets the session cookie
  POST /api/logout   sign out
  GET  /api/state    dashboard data
  GET  /api/config   services for the settings page (secrets masked)
  POST /api/config   save services edited in the settings page

With a host agent (PIHUB_AGENT_SOCKET, see hostagent.py):
  GET  /api/host          agent status and running Claude sessions
  GET  /api/host/dirs     folders to start a session in (?path=)
  POST /api/host/claude   start a session ({"path", "name"})
  POST /api/host/stop     stop a session ({"id"})
  POST /api/host/reboot   restart the machine

With sign-in on (see auth.py), every other /api/ endpoint needs the HttpOnly
session cookie set by /api/login; the static UI stays public so the sign-in
screen can load. PIHUB_READONLY=1 disables saving settings and host actions.
"""

import http.cookies
import json
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol

from .auth import COOKIE_NAME, SESSION_SECONDS, Auth, AuthError, Throttled
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
# Reachable without a session: the probe, and what it takes to sign in.
PUBLIC_API = frozenset({"/api/health", "/api/auth", "/api/login", "/api/logout"})
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
    hub: HubAPI, auth: Auth | None = None, readonly: bool = False, agent: AgentClient | None = None
) -> type[BaseHTTPRequestHandler]:
    """Builds the request handler class bound to one hub and its access settings.

    ``auth`` of None means sign-in is off.
    """
    access = auth or Auth("off")

    class Handler(BaseHTTPRequestHandler):
        server_version = "pi-hub"
        sys_version = ""

        def log_message(self, format: str, *args: Any) -> None:
            """Silences the default per-request stderr log."""
            pass

        def send(
            self,
            code: int,
            body: object,
            ctype: str = "application/json",
            cache: str = "no-store",
            headers: dict[str, str] | None = None,
        ) -> None:
            """Sends a response with the security headers; ``body`` is bytes or JSON-able."""
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", cache)
            for k, v in {**SECURITY_HEADERS, **(headers or {})}.items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def session_token(self) -> str | None:
            """The session token from the request's cookie, if any; a malformed cookie counts as none."""
            jar = http.cookies.SimpleCookie()
            try:
                jar.load(self.headers.get("Cookie", ""))
            except http.cookies.CookieError:
                return None
            morsel = jar.get(COOKIE_NAME)
            return morsel.value if morsel else None

        def signed_in(self) -> bool:
            return not access.enabled or access.session_user(self.session_token()) is not None

        def needs_sign_in(self, path: str) -> bool:
            """True (after answering 401) for an /api/ path the request may not use yet."""
            if path.startswith("/api/") and path not in PUBLIC_API and not self.signed_in():
                self.send(401, {"error": "Sign in required"})
                return True
            return False

        def send_session(self, token: str) -> None:
            """Answers a successful sign-in: the auth status plus the session cookie."""
            cookie = f"{COOKIE_NAME}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_SECONDS}"
            self.send(200, access.status(token), headers={"Set-Cookie": cookie})

        def send_auth_error(self, e: AuthError) -> None:
            headers = {"Retry-After": str(e.retry_after)} if isinstance(e, Throttled) else None
            self.send(e.status, {"error": str(e)}, headers=headers)

        def do_HEAD(self) -> None:
            self.do_GET()

        def do_GET(self) -> None:
            url = urllib.parse.urlparse(self.path)
            path = url.path
            if self.needs_sign_in(path):
                return None
            if path == "/api/health":
                return self.send(200, {"ok": True})
            if path == "/api/auth":
                return self.send(200, access.status(self.session_token()))
            if path == "/api/state":
                return self.send(200, {**hub.state(), "readonly": readonly, "hostAgent": bool(agent)})
            if path == "/api/config":
                return self.send(200, {**hub.settings(), "readonly": readonly})
            if path in ("/api/host", "/api/host/dirs"):
                if not agent:
                    return self.send(404, {"error": "No host agent configured"})
                if path == "/api/host":
                    return self.host_call("status")
                rel = urllib.parse.parse_qs(url.query).get("path", [""])[0]
                return self.host_call("list", path=rel)
            return self.static(path)

        def do_POST(self) -> None:
            path = urllib.parse.urlparse(self.path).path
            if self.needs_sign_in(path):
                return None
            host_action = HOST_ACTIONS.get(path)
            account_action = path in ("/api/login", "/api/logout")
            if path != "/api/config" and not account_action and not (host_action and agent):
                return self.send(404, {"error": "Not found"})
            if readonly and not account_action:
                return self.send(403, {"error": "Settings are read-only (PIHUB_READONLY is set)"})
            # JSON only: stops plain cross-site form posts.
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                return self.send(415, {"error": "Content-Type must be application/json"})
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length > MAX_BODY:
                    return self.send(413, {"error": "Request too large"})
                body = json.loads(self.rfile.read(length) or b"{}")
                if account_action:
                    if not isinstance(body, dict):
                        raise ValueError("Expected a JSON object")
                    return self.account(path, body)
                if host_action:
                    if not isinstance(body, dict):
                        raise ValueError("Expected a JSON object")
                    params = {k: str(body.get(k, "")) for k in host_action[1]}
                    return self.host_call(host_action[0], **params)
                notices = hub.update_services(body)
                return self.send(200, {**hub.settings(), "readonly": readonly, "notices": notices})
            except (ConfigError, ValueError) as e:
                return self.send(400, {"error": str(e)})

        def account(self, path: str, body: dict[str, Any]) -> None:
            """Handles sign-in and sign-out."""
            if path == "/api/logout":
                access.logout(self.session_token())
                cleared = f"{COOKIE_NAME}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
                return self.send(200, {"ok": True}, headers={"Set-Cookie": cleared})
            try:
                user, password = str(body.get("username", "")), str(body.get("password", ""))
                token = access.login(user, password, self.client_address[0])
            except AuthError as e:
                return self.send_auth_error(e)
            return self.send_session(token)

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
    auth: Auth | None = None,
    readonly: bool = False,
    agent: AgentClient | None = None,
) -> ThreadingHTTPServer:
    """Creates (but does not start) the HTTP server; call ``serve_forever`` on it."""
    httpd = ThreadingHTTPServer((host, port), make_handler(hub, auth, readonly, agent))
    httpd.daemon_threads = True
    return httpd
