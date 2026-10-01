"""The polling engine: checks every service, keeps 24h history, builds /api/state."""

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import config as cfgmod
from . import schedule, system
from .integrations import KINDS, Client, Context
from .util import fmt_duration, fmt_speed

INFO_REFRESH_SECONDS = 600
HISTORY_SAVE_SECONDS = 300


class History:
    """Per-service up/down state and hourly response-time buckets for 24h."""

    def __init__(self, path=None):
        self.path = path
        self.data = {}  # id -> {"since": ts, "up": bool, "hours": {hour_ts: [sum_ms, n_up, n_total]}}
        if path:
            try:
                with open(path) as f:
                    self.data = json.load(f)
            except (OSError, ValueError):
                self.data = {}

    def record(self, sid, up, ms, now=None):
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

    def since(self, sid, now=None):
        entry = self.data.get(sid)
        return fmt_duration((time.time() if now is None else now) - entry["since"]) if entry else None

    def bars(self, sid, now=None):
        hours = self.data.get(sid, {}).get("hours", {})
        current = int((time.time() if now is None else now) // 3600 * 3600)
        out = []
        for i in range(23, -1, -1):
            b = hours.get(str(current - i * 3600))
            if not b or not b[2]:
                out.append({"ms": None, "state": "none"})
            elif b[1] == 0:
                out.append({"ms": None, "state": "down"})
            else:
                out.append({"ms": round(b[0] / b[1], 1), "state": "up" if b[1] == b[2] else "partial"})
        return out

    def prune(self, keep_ids):
        for sid in [s for s in self.data if s not in keep_ids]:
            del self.data[sid]

    def save(self):
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.data, f)
        os.replace(tmp, self.path)


class Hub:
    def __init__(self, config_path, history_path=None):
        self.config_path = config_path
        self.cfg = cfgmod.load(config_path)
        self.lock = threading.Lock()
        self.history = History(history_path)
        self.results = {}  # id -> {"up", "ms", "stat", "running"}
        self.info_cache = {}  # id -> {"version", "extra", "at"}
        self.clients = {}  # id -> (target tuple, Client) so login cookies persist
        self.sys = {"cards": [], "uptime": ""}
        self.playing, self.downloads, self.download_speed = [], [], 0.0
        self.cpu = system.CpuMeter()
        self.pool = ThreadPoolExecutor(max_workers=8)
        self.wake = threading.Event()

    # ── config ──
    def update_services(self, payload):
        with self.lock:
            new_cfg, notices = cfgmod.apply_update(self.cfg, payload)
            cfgmod.save(self.config_path, new_cfg)
            self.cfg = new_cfg
            self.info_cache.clear()
            self.history.prune({s["id"] for s in new_cfg["services"]})
        self.wake.set()  # poll right away so the dashboard reflects the change
        return notices

    def reload(self):
        """Re-read config.json if it was edited on disk."""
        try:
            fresh = cfgmod.load(self.config_path)
        except cfgmod.ConfigError as e:
            print(f"config not reloaded: {e}", flush=True)
            return
        with self.lock:
            if fresh != self.cfg:
                self.cfg = fresh
                self.info_cache.clear()

    # ── polling ──
    def client(self, svc):
        target = tuple(svc.get(f) for f in ("scheme", "host", "port", "basePath", "username", "password", "apiKey"))
        cached = self.clients.get(svc["id"])
        if not cached or cached[0] != target:
            cached = (target, Client(svc, cfgmod.resolve_secret))
            self.clients[svc["id"]] = cached
        return cached[1]

    def poll_service(self, svc, ctx):
        integ = KINDS[svc["kind"]]
        if not integ.needs_port and not svc.get("port"):
            stat, running = integ.read(svc) if hasattr(integ, "read") else ("", False)
            return {"up": True, "cron": True, "ms": None, "stat": stat, "running": running}
        client = self.client(svc)
        status, _, ms = client.request(integ.probe_path)
        up = status is not None
        stat = None
        if up:
            try:
                stat = integ.stats(client, ctx)
            except Exception as e:  # an integration bug must not break the poll
                print(f"{svc['name']}: stats failed: {e!r}", flush=True)
        if stat is None:
            stat = f"Online · {ms:.0f} ms" if up else "Not responding"
        return {"up": up, "cron": False, "ms": ms, "stat": stat}

    def refresh_info(self, services):
        stale = [
            s
            for s in services
            if KINDS[s["kind"]].needs_port
            and s.get("port")
            and time.time() - self.info_cache.get(s["id"], {}).get("at", 0) > INFO_REFRESH_SECONDS
        ]

        def fetch(svc):
            try:
                return KINDS[svc["kind"]].info(self.client(svc))
            except Exception:
                return {"version": None, "extra": {}}

        for svc, info in zip(stale, self.pool.map(fetch, stale), strict=True):
            self.info_cache[svc["id"]] = {**info, "at": time.time()}

    def poll_once(self):
        with self.lock:
            services = list(self.cfg["services"])
            system_cfg = self.cfg["system"]
        self.refresh_info(services)
        ctx = Context()
        ctx.cache = self.info_cache
        results = list(self.pool.map(lambda s: self.poll_service(s, ctx), services))
        sys_stats = system.collect(system_cfg, self.cpu)
        with self.lock:
            self.results = {}
            for svc, r in zip(services, results, strict=True):
                if not r["cron"]:
                    self.history.record(svc["id"], r["up"], r["ms"])
                self.results[svc["id"]] = r
            self.sys = sys_stats
            self.playing = ctx.playing
            self.downloads = sorted(ctx.downloads, key=lambda d: -d["pct"])
            self.download_speed = ctx.download_speed

    def run_forever(self):
        last_save = time.time()
        while True:
            try:
                self.reload()
                self.poll_once()
                if time.time() - last_save > HISTORY_SAVE_SECONDS:
                    with self.lock:
                        self.history.save()
                    last_save = time.time()
            except Exception as e:  # keep polling whatever happens
                print(f"poll error: {e!r}", flush=True)
            self.wake.wait(self.cfg["pollSeconds"])
            self.wake.clear()

    # ── API views ──
    def state(self):
        now = time.time()
        with self.lock:
            cfg = self.cfg
            services = []
            for svc in cfg["services"]:
                r = self.results.get(svc["id"])
                integ = KINDS[svc["kind"]]
                cron = not integ.needs_port and not svc.get("port")
                if cron:
                    state, since = "Scheduled", None
                elif r is None:
                    state, since = "Checking", None
                else:
                    state = "Online" if r["up"] else "Offline"
                    since = self.history.since(svc["id"], now)
                services.append(
                    {
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
                        "up": r["up"] if r else None,
                        "cron": cron,
                        "state": state,
                        "since": since,
                        "ms": round(r["ms"]) if r and r.get("ms") is not None else None,
                        "stat": r["stat"] if r else "Checking…",
                        "running": bool(r and r.get("running")),
                        "version": self.info_cache.get(svc["id"], {}).get("version"),
                        "bars": [] if cron else self.history.bars(svc["id"], now),
                    }
                )
            kinds = {s["kind"] for s in cfg["services"]}
            return {
                "title": cfg["title"],
                "sys": self.sys,
                "services": services,
                "categories": cfg["categories"],
                "playing": list(self.playing) if "plex" in kinds else None,
                "downloads": {
                    "speed": fmt_speed(self.download_speed),
                    "items": self.downloads[:4],
                    "total": len(self.downloads),
                }
                if kinds & {"qbittorrent", "sabnzbd"}
                else None,
                "schedule": schedule.entries(cfg["schedule"]),
            }

    def settings(self):
        with self.lock:
            return {
                "services": cfgmod.public_services(self.cfg),
                "categories": self.cfg["categories"],
                "kinds": {
                    k: {
                        "label": v.label,
                        "needsPort": v.needs_port,
                        "defaultPort": v.default_port,
                        "keyHint": v.key_hint,
                    }
                    for k, v in KINDS.items()
                },
            }
