"""Per-application integrations.

Every service has a ``kind``. The kind decides how Pi Hub checks the service is
up, which one-line status it shows on the card ("3 queued · 9 wanted"), and
which version/extra info it fetches occasionally. ``generic`` works for any
web app: it only checks that the port answers.

Adding an integration: subclass ``Integration``, implement ``stats`` and/or
``info``, and register it in ``KINDS`` at the bottom. See CONTRIBUTING.md.

Integrations never share mutable state: ``stats`` runs in a worker thread per
service and *returns* everything it found (a ``Report``); the hub merges the
reports afterwards.
"""

import http.cookiejar
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .util import day_label, fmt_speed, plural


class Client:
    """Tiny HTTP client bound to one service's base URL."""

    def __init__(self, svc, secret=lambda v: v, timeout=5):
        host = svc.get("host") or "127.0.0.1"
        self.base = f"{svc.get('scheme', 'http')}://{host}:{svc['port']}{svc.get('basePath', '')}"
        self.svc = svc
        self.secret = secret
        self.timeout = timeout
        self.cookies = http.cookiejar.CookieJar()
        # Set by integrations that log in (qBittorrent) after a rejected login.
        self.login_failed = False
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))

    @property
    def api_key(self):
        return self.secret(self.svc.get("apiKey", ""))

    def request(self, path, headers=None, data=None, timeout=None):
        """Return (status, body, elapsed_ms). status is None if unreachable."""
        req = urllib.request.Request(self.base + path, data=data, headers={"User-Agent": "pi-hub", **(headers or {})})
        start = time.monotonic()
        try:
            with self.opener.open(req, timeout=timeout or self.timeout) as r:
                return r.status, r.read(), (time.monotonic() - start) * 1000
        except urllib.error.HTTPError as e:
            return e.code, b"", (time.monotonic() - start) * 1000
        except Exception:
            return None, None, None

    def json(self, path, headers=None):
        status, body, _ = self.request(path, {"Accept": "application/json", **(headers or {})})
        if status != 200 or not body:
            return None
        try:
            return json.loads(body)
        except ValueError:
            return None


@dataclass
class Report:
    """What one service contributed during a poll.

    ``status`` is the card's one-line status. ``playing`` and ``downloads``
    feed the sidebar; they are only read for integrations that list the
    matching section in ``Integration.provides``.
    """

    status: str | None = None
    playing: list = field(default_factory=list)
    downloads: list = field(default_factory=list)
    download_speed: float = 0.0


class Integration:
    label = "Web app"
    needs_port = True
    probe_path = "/"
    default_port = ""
    # Shown in the settings UI next to the API key field.
    key_hint = ""
    # Sidebar sections this kind can fill: "playing" and/or "downloads". The
    # dashboard shows a section whenever a configured service provides it.
    provides: frozenset[str] = frozenset()

    def stats(self, client, info):
        """Status for the card, polled every few seconds.

        ``info`` is this service's latest ``info()`` result. Return a string,
        a ``Report`` when there is sidebar data too, or None to fall back to
        "Online · 12 ms".
        """
        return None

    def local_status(self, svc):
        """Status for kinds checked without HTTP (e.g. by reading a log file).

        Return ``(status, running)`` or None. Only called for scheduled
        services; see ``is_scheduled``.
        """
        return None

    def info(self, client):
        """Slow-changing details: {'version': str|None, 'extra': {...}}."""
        return {"version": None, "extra": {}}


class Generic(Integration):
    pass


class Arr(Integration):
    """Sonarr / Radarr (API v3)."""

    api = "v3"
    missing_word = "wanted"

    def _h(self, client):
        return {"X-Api-Key": client.api_key}

    def stats(self, client, info):
        if not client.api_key:
            return None
        q = client.json(f"/api/{self.api}/queue?pageSize=1", self._h(client))
        miss = client.json(f"/api/{self.api}/wanted/missing?pageSize=1&monitored=true", self._h(client))
        if q is None and miss is None:
            return None
        queued = q.get("totalRecords", 0) if q else "?"
        missing = miss.get("totalRecords", 0) if miss else "?"
        return f"{queued} queued · {missing} {self.missing_word}"

    def info(self, client):
        status = client.json(f"/api/{self.api}/system/status", self._h(client)) or {}
        return {"version": status.get("version"), "extra": {}}


class Sonarr(Arr):
    label = "Sonarr"
    default_port = "8989"
    missing_word = "missing"
    key_hint = "Settings → General → API Key"


class Radarr(Arr):
    label = "Radarr"
    default_port = "7878"
    key_hint = "Settings → General → API Key"


class Prowlarr(Integration):
    label = "Prowlarr"
    default_port = "9696"
    key_hint = "Settings → General → API Key"

    def stats(self, client, info):
        if not client.api_key:
            return None
        h = {"X-Api-Key": client.api_key}
        indexers = client.json("/api/v1/indexer", h)
        if indexers is None:
            return None
        status = client.json("/api/v1/indexerstatus", h) or []
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        failing = {s["indexerId"] for s in status if (s.get("disabledTill") or "") > now}
        enabled = [i["id"] for i in indexers if i.get("enable")]
        healthy = sum(1 for i in enabled if i not in failing)
        return f"{healthy}/{len(enabled)} indexers healthy"

    def info(self, client):
        status = client.json("/api/v1/system/status", {"X-Api-Key": client.api_key}) or {}
        return {"version": status.get("version"), "extra": {}}


class Bazarr(Integration):
    label = "Bazarr"
    default_port = "6767"
    key_hint = "Settings → General → Security → API Key"

    def stats(self, client, info):
        if not client.api_key:
            return None
        badges = client.json("/api/badges", {"X-API-KEY": client.api_key})
        if badges is None:
            return None
        return f"{badges.get('episodes', 0):,} episodes · {badges.get('movies', 0):,} movies wanted"

    def info(self, client):
        status = client.json("/api/system/status", {"X-API-KEY": client.api_key}) or {}
        return {"version": (status.get("data") or {}).get("bazarr_version"), "extra": {}}


class QBittorrent(Integration):
    label = "qBittorrent"
    default_port = "8080"
    provides = frozenset({"downloads"})
    key_hint = "Web UI password (leave blank if localhost auth bypass is on)"

    LOGIN_FAILED = "Login failed — check username and password"

    def _login(self, client):
        """Log in once per Client; a failure is remembered so it is never retried.

        qBittorrent bans an IP after a few failed logins (5 by default), so
        retrying a wrong password on every poll would lock the host out. The hub
        builds a new Client whenever the credentials change, which is what
        clears ``login_failed``.
        """
        if client.login_failed:
            return False
        data = urllib.parse.urlencode(
            {"username": client.svc["username"], "password": client.secret(client.svc.get("password", ""))}
        )
        status, body, _ = client.request("/api/v2/auth/login", {"Referer": client.base}, data.encode())
        ok = status == 200 and (body or b"").strip() == b"Ok."
        client.login_failed = not ok and status is not None
        return ok

    def _json(self, client, path):
        result = client.json(path)
        if result is None and client.svc.get("username") and self._login(client):
            result = client.json(path)
        return result

    def stats(self, client, info):
        transfer = self._json(client, "/api/v2/transfer/info")
        if transfer is None:
            return self.LOGIN_FAILED if client.login_failed else None
        items = self._json(client, "/api/v2/torrents/info?filter=downloading") or []
        speed = transfer.get("dl_info_speed", 0)
        active = sum(1 for t in items if t.get("dlspeed", 0) > 0)
        waiting = len(items) - active
        return Report(
            status=f"↓ {fmt_speed(speed)} · {active} active" + (f" · {waiting} queued" if waiting else ""),
            downloads=[{"name": t["name"], "pct": t["progress"] * 100, "src": "qBittorrent"} for t in items],
            download_speed=speed,
        )

    def info(self, client):
        status, body, _ = client.request("/api/v2/app/version")
        if status == 403 and client.svc.get("username") and self._login(client):
            status, body, _ = client.request("/api/v2/app/version")
        return {"version": body.decode().strip().lstrip("v") if status == 200 and body else None, "extra": {}}


class SABnzbd(Integration):
    label = "SABnzbd"
    default_port = "8085"
    provides = frozenset({"downloads"})
    key_hint = "Config → General → API Key"

    def stats(self, client, info):
        if not client.api_key:
            return None
        data = client.json(f"/api?mode=queue&output=json&apikey={urllib.parse.quote(client.api_key)}")
        if not data or "queue" not in data:
            return None
        q = data["queue"]
        speed = float(q.get("kbpersec") or 0) * 1000
        n = int(q.get("noofslots", 0))
        if q.get("paused") or q.get("status") == "Paused":
            status = f"Paused · {plural(n, 'item')} waiting"
        else:
            status = f"↓ {fmt_speed(speed)} · {n} active" if n else "Idle"
        downloads = [
            {"name": s.get("filename", "?"), "pct": float(s.get("percentage", 0)), "src": "SABnzbd"}
            for s in q.get("slots", [])
        ]
        return Report(status=status, downloads=downloads, download_speed=speed)

    def info(self, client):
        return {"version": (client.json("/api?mode=version&output=json") or {}).get("version"), "extra": {}}


class Plex(Integration):
    label = "Plex"
    default_port = "32400"
    probe_path = "/identity"
    provides = frozenset({"playing"})
    key_hint = "X-Plex-Token (see Plex support: 'Finding an authentication token')"

    def stats(self, client, info):
        if not client.api_key:
            return None
        sessions = client.json("/status/sessions", {"X-Plex-Token": client.api_key})
        if sessions is None:
            return None
        mc = sessions.get("MediaContainer", {})
        titles = (info or {}).get("extra", {}).get("titles")
        return Report(
            status=plural(int(mc.get("size", 0)), "stream") + (f" · {titles:,} titles" if titles is not None else ""),
            playing=[self._session(m) for m in mc.get("Metadata", []) or []],
        )

    @staticmethod
    def _session(m):
        if m.get("type") == "episode":
            title = f"{m.get('grandparentTitle', '')} S{m.get('parentIndex', 0):02d}E{m.get('index', 0):02d}"
        else:
            title = m.get("title", "?") + (f" ({m['year']})" if m.get("year") else "")
        ts = m.get("TranscodeSession")
        if ts and ts.get("videoDecision") == "transcode":
            how = "Transcode" + (f" {ts['height']}p" if ts.get("height") else "")
        else:
            how = "Direct stream" if ts else "Direct play"
        player = m.get("Player") or {}
        duration = m.get("duration") or 0
        return {
            "title": title,
            "who": (m.get("User") or {}).get("title") or player.get("title", "?"),
            "how": how,
            "pct": (m.get("viewOffset", 0) / duration * 100) if duration else 0,
            "state": player.get("state", "playing"),
        }

    def info(self, client):
        ident = (client.json("/identity") or {}).get("MediaContainer") or {}
        version = (ident.get("version") or "").split("-")[0] or None
        extra = {}
        if client.api_key:
            h = {"X-Plex-Token": client.api_key}
            sections = ((client.json("/library/sections", h) or {}).get("MediaContainer") or {}).get("Directory", [])
            total = 0
            for s in sections:
                if s.get("type") in ("movie", "show"):
                    c = client.json(
                        f"/library/sections/{s['key']}/all?X-Plex-Container-Start=0&X-Plex-Container-Size=0", h
                    )
                    total += ((c or {}).get("MediaContainer") or {}).get("totalSize", 0)
            if sections:
                extra["titles"] = total
        return {"version": version, "extra": extra}


_KOMETA_RUN = re.compile(
    r"Start Time: (\d\d:\d\d)(?::\d\d)? (\d{4}-\d\d-\d\d)\s+Finished: (\d\d:\d\d)(?::\d\d)? "
    r"(\d{4}-\d\d-\d\d)\s+Run Time: ([\d:]+)"
)


class Kometa(Integration):
    """Kometa runs on a schedule, so status comes from its log file, not HTTP."""

    label = "Kometa"
    needs_port = False

    @staticmethod
    def parse_log(text, mtime, now=None):
        now = time.time() if now is None else now
        if now - mtime < 120 and "Finished Run" not in text[-4000:]:
            return "Running now", True
        runs = _KOMETA_RUN.findall(text)
        if not runs:
            return "No completed run yet", False
        _, _, end_time, end_date, runtime = runs[-1]
        hours, minutes, _ = (int(x) for x in runtime.split(":"))
        took = f"{hours}h {minutes}m" if hours else f"{minutes}m"
        day = day_label(datetime.strptime(end_date, "%Y-%m-%d"), datetime.fromtimestamp(now))
        return f"Last run {day} {end_time} · took {took}", False

    def local_status(self, svc):
        path = svc.get("log")
        if not path:
            return 'Set "log" to Kometa\'s meta.log', False
        try:
            st = os.stat(path)
            with open(path, "rb") as f:
                f.seek(max(st.st_size - 300_000, 0))
                text = f.read().decode("utf-8", "replace")
        except OSError:
            return "Log not found", False
        return self.parse_log(text, st.st_mtime)


KINDS = {
    "generic": Generic(),
    "plex": Plex(),
    "sonarr": Sonarr(),
    "radarr": Radarr(),
    "prowlarr": Prowlarr(),
    "bazarr": Bazarr(),
    "qbittorrent": QBittorrent(),
    "sabnzbd": SABnzbd(),
    "kometa": Kometa(),
}


def is_scheduled(svc):
    """True for services that are checked locally instead of over HTTP.

    These are kinds that need no port (Kometa) and that the user has not given
    a port anyway. They show "Scheduled" and have no uptime history.
    """
    return not KINDS[svc["kind"]].needs_port and not svc.get("port")


def kinds_info():
    """Kind metadata for the settings UI's Kind dropdown and API key hints."""
    return {
        name: {"label": k.label, "needsPort": k.needs_port, "defaultPort": k.default_port, "keyHint": k.key_hint}
        for name, k in KINDS.items()
    }
