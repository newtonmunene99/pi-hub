"""The polling engine: checks every service, keeps 24h history, builds /api/state."""

import json
import logging
import os
import threading
import time
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import config as cfgmod
from . import schedule, system
from .integrations import KINDS, Client, Report, is_scheduled, kinds_info
from .models import Config, Info, Service
from .util import fmt_duration, fmt_speed

logger = logging.getLogger(__name__)

INFO_REFRESH_SECONDS = 600
HISTORY_SAVE_SECONDS = 300


class History:
    """Per-service up/down state and hourly response-time buckets for 24 hours.

    ``data`` maps a service id to ``{"since": ts, "up": bool, "hours": {...}}``.
    ``since`` is when the service last changed between up and down. ``hours``
    maps the start of each hour (epoch seconds, as a string so it survives
    JSON) to a bucket ``[sum_ms, n_up, n_total]``: the total response time of
    successful checks, how many succeeded, and how many were made. Buckets
    older than 25 hours are dropped as new ones are written.
    """

    def __init__(self, path: str | None = None) -> None:
        """Loads history from ``path`` if it exists; a missing or corrupt file starts empty."""
        self.path = path
        self.data: dict[str, dict[str, Any]] = {}
        if path:
            try:
                with open(path) as f:
                    self.data = json.load(f)
            except (OSError, ValueError):
                self.data = {}

    def record(self, sid: str, up: bool, ms: float | None, now: float | None = None) -> None:
        """Adds one check result to the current hour's bucket."""
        now = time.time() if now is None else now
        entry = self.data.setdefault(sid, {"since": now, "up": up, "hours": {}})
        if entry.get("up") != up:
            entry["up"], entry["since"] = up, now
        bucket = entry["hours"].setdefault(str(int(now // 3600 * 3600)), [0.0, 0, 0])
        bucket[2] += 1
        if up and ms is not None:
            bucket[0] += ms
            bucket[1] += 1
        cutoff = now - 25 * 3600
        for key in [k for k in entry["hours"] if int(k) < cutoff]:
            del entry["hours"][key]

    def since(self, sid: str, now: float | None = None) -> str | None:
        """How long the service has been in its current up/down state, e.g. "3d 4h"."""
        entry = self.data.get(sid)
        return fmt_duration((time.time() if now is None else now) - entry["since"]) if entry else None

    def bars(self, sid: str, now: float | None = None) -> list[dict[str, Any]]:
        """24 hourly bars, oldest first, each ``{"ms": avg or None, "state": ...}``.

        ``state`` is "up" (every check succeeded), "partial", "down" (none
        succeeded) or "none" (no checks that hour).
        """
        hours = self.data.get(sid, {}).get("hours", {})
        current = int((time.time() if now is None else now) // 3600 * 3600)
        out: list[dict[str, Any]] = []
        for i in range(23, -1, -1):
            b = hours.get(str(current - i * 3600))
            if not b or not b[2]:
                out.append({"ms": None, "state": "none"})
            elif b[1] == 0:
                out.append({"ms": None, "state": "down"})
            else:
                out.append({"ms": round(b[0] / b[1], 1), "state": "up" if b[1] == b[2] else "partial"})
        return out

    def prune(self, keep_ids: Iterable[str]) -> None:
        """Forgets services that are no longer configured."""
        keep_ids = set(keep_ids)
        for sid in [s for s in self.data if s not in keep_ids]:
            del self.data[sid]

    def save(self) -> None:
        """Writes history atomically (temp file + rename); a no-op without a path."""
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.data, f)
        os.replace(tmp, self.path)


class Hub:
    """Polls every configured service and serves the results to the API.

    ``run_forever`` polls in a background thread; the HTTP threads call
    ``state``, ``settings`` and ``update_services``. Everything they share is
    guarded by ``lock``. Service checks run in a small thread pool and return
    their results, which are merged under the lock (see ``poll_once``).
    """

    def __init__(self, config_path: str, history_path: str | None = None) -> None:
        """Loads the config and history.

        Raises:
            ConfigError: The config file is missing or invalid.
        """
        self.config_path = config_path
        self.cfg: Config = cfgmod.load(config_path)
        self.lock = threading.Lock()
        self.history = History(history_path)
        # Latest check per service id: up, ms, stat, cron, running.
        self.results: dict[str, dict[str, Any]] = {}
        # Latest Integration.info() per service id, refreshed every 10 minutes.
        self.info_cache: dict[str, Info] = {}
        # One Client per service id, keyed by its connection settings, so login
        # cookies survive between polls but are dropped when the target changes.
        self.clients: dict[str, tuple[tuple[object, ...], Client]] = {}
        self.sys: dict[str, Any] = {"cards": [], "uptime": ""}
        self.playing: list[dict[str, Any]] = []
        self.downloads: list[dict[str, Any]] = []
        self.download_speed = 0.0
        self.cpu = system.CpuMeter()
        self.pool = ThreadPoolExecutor(max_workers=8)
        self.wake = threading.Event()

    # ── config ──
    def update_services(self, payload: dict[str, Any]) -> list[str]:
        """Applies an edit from the settings page, saves it, and polls at once.

        Returns:
            Notices for the user (see ``config.apply_update``).

        Raises:
            ConfigError: The edit is invalid; nothing is saved.
        """
        with self.lock:
            new_cfg, notices = cfgmod.apply_update(self.cfg, payload)
            cfgmod.save(self.config_path, new_cfg)
            self.cfg = new_cfg
            self.info_cache.clear()
            self.history.prune(s["id"] for s in new_cfg["services"])
        # Poll right away so the dashboard reflects the change.
        self.wake.set()
        return notices

    def reload(self) -> None:
        """Re-reads config.json so hand edits apply without a restart.

        An invalid file is logged and ignored; the last good config stays live.
        """
        try:
            fresh = cfgmod.load(self.config_path)
        except cfgmod.ConfigError as e:
            logger.warning("config not reloaded: %s", e)
            return
        with self.lock:
            if fresh != self.cfg:
                self.cfg = fresh
                self.info_cache.clear()

    # ── polling ──
    def client(self, svc: Service) -> Client:
        """The cached Client for a service, rebuilt when its address or credentials change."""
        target = tuple(svc.get(f) for f in ("scheme", "host", "port", "basePath", "username", "password", "apiKey"))
        cached = self.clients.get(svc["id"])
        if not cached or cached[0] != target:
            cached = (target, Client(svc, cfgmod.resolve_secret))
            self.clients[svc["id"]] = cached
        return cached[1]

    def poll_service(self, svc: Service) -> dict[str, Any]:
        """Checks one service. Runs in a worker thread, so it only returns results.

        Returns:
            ``{"up", "cron", "ms", "stat", "report"}`` plus ``running`` for
            scheduled services. ``report`` is removed again by ``poll_once``.
        """
        integ = KINDS[svc["kind"]]
        if is_scheduled(svc):
            stat, running = integ.local_status(svc) or ("", False)
            return {"up": True, "cron": True, "ms": None, "stat": stat, "running": running, "report": Report()}
        client = self.client(svc)
        status, _, ms = client.request(integ.probe_path)
        up = status is not None
        report = Report()
        if up:
            # A bug in one integration must not break the poll for the others,
            # so this is the one place a broad except is right; log the traceback.
            try:
                result = integ.stats(client, self.info_cache.get(svc["id"]))
            except Exception:
                logger.exception("%s: stats failed", svc["name"])
            else:
                report = result if isinstance(result, Report) else Report(status=result)
        stat = report.status or (f"Online · {ms:.0f} ms" if up else "Not responding")
        return {"up": up, "cron": False, "ms": ms, "stat": stat, "report": report}

    def refresh_info(self, services: Sequence[Service]) -> None:
        """Fetches version and other slow-changing info for services whose cache is stale."""
        stale = [
            s
            for s in services
            if not is_scheduled(s)
            and time.time() - self.info_cache.get(s["id"], {"version": None, "extra": {}}).get("at", 0)
            > INFO_REFRESH_SECONDS
        ]

        def fetch(svc: Service) -> Info:
            try:
                return KINDS[svc["kind"]].info(self.client(svc))
            except Exception:
                logger.exception("%s: info failed", svc["name"])
                return {"version": None, "extra": {}}

        for svc, info in zip(stale, self.pool.map(fetch, stale), strict=True):
            self.info_cache[svc["id"]] = {**info, "at": time.time()}

    def poll_once(self) -> None:
        """Checks every service once and publishes the results."""
        with self.lock:
            services = list(self.cfg["services"])
            system_cfg = self.cfg["system"]
        self.refresh_info(services)
        results = list(self.pool.map(self.poll_service, services))
        sys_stats = system.collect(system_cfg, self.cpu)
        # Merge sidebar data here, in one thread, after all workers finished.
        reports = [r.pop("report") for r in results]
        with self.lock:
            self.results = {}
            for svc, r in zip(services, results, strict=True):
                if not r["cron"]:
                    self.history.record(svc["id"], r["up"], r["ms"])
                self.results[svc["id"]] = r
            self.sys = sys_stats
            self.playing = [item for rep in reports for item in rep.playing]
            self.downloads = sorted((d for rep in reports for d in rep.downloads), key=lambda d: -d["pct"])
            self.download_speed = sum(rep.download_speed for rep in reports)

    def run_forever(self) -> None:
        """Polls until the process exits; meant for a daemon thread.

        Waits ``pollSeconds`` between polls, or less when ``update_services``
        asks for an immediate poll.
        """
        last_save = time.time()
        while True:
            try:
                self.reload()
                self.poll_once()
                if time.time() - last_save > HISTORY_SAVE_SECONDS:
                    with self.lock:
                        self.history.save()
                    last_save = time.time()
            # The poll thread must survive anything, or the dashboard freezes.
            except Exception:
                logger.exception("poll failed")
            self.wake.wait(self.cfg["pollSeconds"])
            self.wake.clear()

    # ── API views ──
    def state(self) -> dict[str, Any]:
        """The /api/state document."""
        now = time.time()
        with self.lock:
            cfg = self.cfg
            services = []
            for svc in cfg["services"]:
                r = self.results.get(svc["id"])
                if is_scheduled(svc):
                    state, since = "Scheduled", None
                elif r is None:
                    state, since = "Checking", None
                else:
                    state = "Online" if r["up"] else "Offline"
                    since = self.history.since(svc["id"], now)
                services.append(
                    service_view(
                        svc,
                        up=r["up"] if r else None,
                        state=state,
                        since=since,
                        ms=round(r["ms"]) if r and r.get("ms") is not None else None,
                        stat=r["stat"] if r else "Checking…",
                        running=bool(r and r.get("running")),
                        version=info["version"] if (info := self.info_cache.get(svc["id"])) else None,
                        bars=[] if is_scheduled(svc) else self.history.bars(svc["id"], now),
                    )
                )
            return state_view(
                cfg,
                services,
                sys_stats=self.sys,
                playing=list(self.playing),
                downloads=self.downloads,
                download_speed=self.download_speed,
            )

    def settings(self) -> dict[str, Any]:
        """The /api/config document (secrets masked)."""
        with self.lock:
            return settings_view(self.cfg)


def service_view(svc: Service, **live: Any) -> dict[str, Any]:
    """One service as the dashboard sees it: config fields plus ``live`` status fields."""
    return {
        "id": svc["id"],
        "name": svc["name"],
        "icon": svc["icon"],
        "category": svc["category"],
        "kind": svc["kind"],
        "description": svc["description"],
        "pinned": svc["pinned"],
        "scheme": svc["scheme"],
        "port": svc["port"],
        "linkPath": svc["basePath"] + svc["linkPath"],
        "url": svc["url"],
        "cron": is_scheduled(svc),
        **live,
    }


def state_view(
    cfg: Config,
    services: list[dict[str, Any]],
    *,
    sys_stats: dict[str, Any],
    playing: list[dict[str, Any]],
    downloads: list[dict[str, Any]],
    download_speed: float,
) -> dict[str, Any]:
    """The /api/state document. Sidebar sections are None when no service provides them."""
    provided: set[str] = set().union(*(KINDS[s["kind"]].provides for s in cfg["services"]))
    return {
        "title": cfg["title"],
        "sys": sys_stats,
        "services": services,
        "categories": cfg["categories"],
        "playing": playing if "playing" in provided else None,
        "downloads": {"speed": fmt_speed(download_speed), "items": downloads[:4], "total": len(downloads)}
        if "downloads" in provided
        else None,
        "schedule": schedule.entries(cfg["schedule"]),
    }


def settings_view(cfg: Config) -> dict[str, Any]:
    """The /api/config document: services with secrets masked, plus kind metadata."""
    return {
        "services": cfgmod.public_services(cfg),
        "categories": cfg["categories"],
        "kinds": kinds_info(),
    }
