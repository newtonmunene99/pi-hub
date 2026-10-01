"""Demo mode (PIHUB_DEMO=1): realistic fake data, no services or config needed.

Used for working on the UI and for README screenshots. Settings edits are kept
in memory only.
"""

import random
import threading
import time
import zlib

from . import config as cfgmod
from .hub import service_view, settings_view, state_view
from .integrations import is_scheduled
from .util import fmt_duration

DEMO_CONFIG = {
    "title": "pi-hub",
    "services": [
        {
            "name": "Plex",
            "kind": "plex",
            "category": "Media",
            "port": "32400",
            "linkPath": "/web",
            "description": "Media server",
            "pinned": True,
            "apiKey": "demo",
        },
        {
            "name": "Overseerr",
            "category": "Media",
            "port": "5055",
            "icon": "O",
            "description": "Requests",
            "pinned": True,
        },
        {"name": "Tautulli", "category": "Media", "port": "8181", "description": "Plex analytics"},
        {"name": "Kometa", "kind": "kometa", "category": "Media", "description": "Collections & overlays"},
        {
            "name": "Sonarr",
            "kind": "sonarr",
            "category": "Automation",
            "port": "8989",
            "description": "TV",
            "pinned": True,
        },
        {
            "name": "Radarr",
            "kind": "radarr",
            "category": "Automation",
            "port": "7878",
            "description": "Movies",
            "pinned": True,
        },
        {
            "name": "Prowlarr",
            "kind": "prowlarr",
            "category": "Automation",
            "port": "9696",
            "icon": "Pr",
            "description": "Indexers",
        },
        {"name": "Bazarr", "kind": "bazarr", "category": "Automation", "port": "6767", "description": "Subtitles"},
        {
            "name": "qBittorrent",
            "kind": "qbittorrent",
            "category": "Downloads",
            "port": "8080",
            "icon": "q",
            "description": "Torrents",
        },
        {
            "name": "SABnzbd",
            "kind": "sabnzbd",
            "category": "Downloads",
            "port": "8085",
            "icon": "SA",
            "description": "Usenet",
        },
        {"name": "Portainer", "category": "System", "port": "9000", "icon": "Po", "description": "Docker"},
        {"name": "Netdata", "category": "System", "port": "19999", "description": "Live metrics"},
    ],
    "schedule": [{"name": "Kometa", "at": "03:00", "service": "kometa"}, {"name": "Config backup", "at": "05:00"}],
}

STATS = {
    "plex": "2 streams · 4,812 titles",
    "overseerr": "3 pending requests",
    "tautulli": "126 plays this week",
    "kometa": "Last run today 03:00 · took 59m",
    "sonarr": "12 queued · 4 missing",
    "radarr": "3 queued · 9 wanted",
    "prowlarr": "7/8 indexers healthy",
    "bazarr": "5 episodes · 2 movies wanted",
    "qbittorrent": "↓ 14.2 MB/s · 6 active",
    "sabnzbd": "↓ 4.2 MB/s · 2 active",
    "portainer": "Online · 9 ms",
    "netdata": "Not responding",
}
VERSIONS = {
    "plex": "1.43.4",
    "sonarr": "4.0.20",
    "radarr": "6.4.4",
    "prowlarr": "2.6.5",
    "bazarr": "1.6.2",
    "qbittorrent": "5.1.0",
    "sabnzbd": "4.5.0",
    "overseerr": "1.34.0",
    "portainer": "2.27.1",
}


class DemoHub:
    def __init__(self):
        self.cfg = cfgmod.normalise(DEMO_CONFIG)
        self.lock = threading.Lock()
        self.started = time.time()

    def update_services(self, payload):
        with self.lock:
            self.cfg, notices = cfgmod.apply_update(self.cfg, payload)
        return notices

    def settings(self):
        with self.lock:
            return settings_view(self.cfg)

    def state(self):
        rng = random.Random(int(time.time() // 15))  # noqa: S311 - jitter for fake demo data
        services = []
        for svc in self.cfg["services"]:
            seed = zlib.crc32(svc["id"].encode())
            up = svc["id"] != "netdata"
            cron = is_scheduled(svc)
            ms = 18 + seed % 70
            bars = [
                {"ms": None, "state": "down"}
                if not up and i > 20
                else {"ms": ms + (seed * (i + 3)) % 40, "state": "up"}
                for i in range(24)
            ]
            services.append(
                service_view(
                    svc,
                    up=True if cron else up,
                    state="Scheduled" if cron else ("Online" if up else "Offline"),
                    since=fmt_duration((12 + seed % 30) * 86400 + seed % 80000) if up else "2h 14m",
                    ms=None if cron or not up else ms + rng.randint(-4, 4),
                    stat=STATS.get(svc["id"], "Online · 12 ms"),
                    running=False,
                    version=VERSIONS.get(svc["id"]),
                    bars=[] if cron else bars,
                )
            )
        sys_stats = {
            "uptime": "41d 6h",
            "cards": [
                {
                    "label": "CPU temp",
                    "value": f"{52 + rng.randint(-1, 1)}°",
                    "sub": "Pi 5 · fan 30%",
                    "pct": 61,
                    "warn": False,
                },
                {
                    "label": "CPU load",
                    "value": f"{38 + rng.randint(-5, 5)}%",
                    "sub": "4 cores · 1.20 load",
                    "pct": 38,
                    "warn": False,
                },
                {"label": "Memory", "value": "5.1", "sub": "of 8 GB", "pct": 64, "warn": False},
                {"label": "media", "value": "2.7", "sub": "of 4.0 TB · 68%", "pct": 68, "warn": False},
            ],
        }
        return state_view(
            self.cfg,
            services,
            sys_stats=sys_stats,
            playing=[
                {"title": "Severance S02E04", "who": "Sam", "how": "Direct play", "pct": 42, "state": "playing"},
                {
                    "title": "Dune: Part Two (2024)",
                    "who": "Living room TV",
                    "how": "Transcode 1080p",
                    "pct": 71,
                    "state": "playing",
                },
            ],
            downloads=[
                {"name": "The.Bear.S03E05.1080p", "pct": 84, "src": "qBittorrent"},
                {"name": "Anora.2024.2160p.WEB", "pct": 37, "src": "qBittorrent"},
                {"name": "Andor.S02E09.1080p", "pct": 12, "src": "SABnzbd"},
            ],
            download_speed=18_400_000,
        )
