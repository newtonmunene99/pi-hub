"""Integrations against a fake HTTP server that mimics each app's API."""

import json
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

from pihub.integrations import KINDS, Client, Context, Kometa

ROUTES = {
    "/api/v3/queue": {"totalRecords": 3},
    "/api/v3/wanted/missing": {"totalRecords": 9},
    "/api/v3/system/status": {"version": "6.4.4"},
    "/api/v1/indexer": [{"id": 1, "enable": True}, {"id": 2, "enable": True}, {"id": 3, "enable": False}],
    "/api/v1/indexerstatus": [{"indexerId": 2, "disabledTill": "2999-01-01T00:00:00Z"}],
    "/api/badges": {"episodes": 1234, "movies": 5},
    "/api/v2/transfer/info": {"dl_info_speed": 14_200_000},
    "/api/v2/torrents/info": [
        {"name": "A", "progress": 0.5, "dlspeed": 100},
        {"name": "B", "progress": 0.1, "dlspeed": 0},
    ],
    "/status/sessions": {
        "MediaContainer": {
            "size": 1,
            "Metadata": [
                {
                    "type": "episode",
                    "grandparentTitle": "Andor",
                    "parentIndex": 2,
                    "index": 9,
                    "duration": 1000,
                    "viewOffset": 250,
                    "User": {"title": "sam"},
                    "Player": {"state": "playing"},
                    "TranscodeSession": {"videoDecision": "transcode", "height": 1080},
                }
            ],
        }
    },
}


class FakeApps(BaseHTTPRequestHandler):
    requests: ClassVar[list] = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        FakeApps.requests.append((url.path, dict(self.headers)))
        if url.path == "/api":  # SABnzbd
            q = urllib.parse.parse_qs(url.query)
            if q.get("apikey") != ["sabkey"] and q.get("mode") != ["version"]:
                return self._send(403, {})
            body = (
                {"version": "4.5.0"}
                if q["mode"] == ["version"]
                else {
                    "queue": {
                        "status": "Downloading",
                        "kbpersec": "4200",
                        "noofslots": "1",
                        "slots": [{"filename": "Some.Show", "percentage": "40"}],
                    }
                }
            )
            return self._send(200, body)
        if url.path in ROUTES:
            return self._send(200, ROUTES[url.path])
        return self._send(404, {})

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeApps)
        cls.port = str(cls.server.server_address[1])
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def client(self, kind, **extra):
        return Client({"id": kind, "kind": kind, "port": self.port, "host": "127.0.0.1", **extra})

    def test_radarr_sonarr(self):
        ctx = Context()
        self.assertEqual(KINDS["radarr"].stats(self.client("radarr", apiKey="k"), ctx), "3 queued · 9 wanted")
        self.assertEqual(KINDS["sonarr"].stats(self.client("sonarr", apiKey="k"), ctx), "3 queued · 9 missing")
        self.assertEqual(KINDS["radarr"].info(self.client("radarr", apiKey="k"))["version"], "6.4.4")
        sent = [h for p, h in FakeApps.requests if p == "/api/v3/queue"][-1]
        self.assertEqual(sent.get("X-Api-Key"), "k")

    def test_no_key_means_no_stats_and_no_request(self):
        before = len(FakeApps.requests)
        self.assertIsNone(KINDS["radarr"].stats(self.client("radarr"), Context()))
        self.assertEqual(len(FakeApps.requests), before)

    def test_prowlarr_counts_failing_indexers(self):
        self.assertEqual(
            KINDS["prowlarr"].stats(self.client("prowlarr", apiKey="k"), Context()), "1/2 indexers healthy"
        )

    def test_bazarr(self):
        self.assertEqual(
            KINDS["bazarr"].stats(self.client("bazarr", apiKey="k"), Context()), "1,234 episodes · 5 movies wanted"
        )

    def test_qbittorrent_feeds_download_sidebar(self):
        ctx = Context()
        self.assertEqual(
            KINDS["qbittorrent"].stats(self.client("qbittorrent"), ctx), "↓ 14.2 MB/s · 1 active · 1 queued"
        )
        self.assertEqual(len(ctx.downloads), 2)
        self.assertEqual(ctx.download_speed, 14_200_000)

    def test_sabnzbd(self):
        ctx = Context()
        self.assertEqual(KINDS["sabnzbd"].stats(self.client("sabnzbd", apiKey="sabkey"), ctx), "↓ 4.2 MB/s · 1 active")
        self.assertIsNone(KINDS["sabnzbd"].stats(self.client("sabnzbd", apiKey="wrong"), Context()))
        self.assertEqual(ctx.downloads[0]["name"], "Some.Show")

    def test_plex_sessions(self):
        ctx = Context()
        ctx.cache = {"plex": {"extra": {"titles": 4812}}}
        self.assertEqual(KINDS["plex"].stats(self.client("plex", apiKey="tok"), ctx), "1 stream · 4,812 titles")
        self.assertEqual(ctx.playing[0]["title"], "Andor S02E09")
        self.assertEqual(ctx.playing[0]["how"], "Transcode 1080p")
        self.assertEqual(ctx.playing[0]["pct"], 25)

    def test_secret_resolver_is_used(self):
        c = Client(
            {"id": "r", "port": self.port, "apiKey": "env:X"}, secret=lambda v: "resolved" if v == "env:X" else v
        )
        self.assertEqual(c.api_key, "resolved")

    def test_unreachable_service(self):
        c = Client({"id": "x", "port": "1", "host": "127.0.0.1"}, timeout=1)
        self.assertEqual(c.request("/"), (None, None, None))


class KometaTests(unittest.TestCase):
    LOG = "| Start Time: 03:00:01 2026-09-30     Finished: 03:59:40 2026-09-30     Run Time: 0:59:39 |\nFinished Run\n"

    def test_last_run(self):
        now = time.mktime((2026, 9, 30, 12, 0, 0, 0, 0, -1))
        self.assertEqual(
            Kometa.parse_log(self.LOG, mtime=now - 3600, now=now), ("Last run today 03:59 · took 59m", False)
        )

    def test_running(self):
        self.assertEqual(Kometa.parse_log("Starting run...", mtime=time.time())[1], True)

    def test_missing_log(self):
        self.assertEqual(Kometa().read({"log": "/nonexistent/meta.log"}), ("Log not found", False))


if __name__ == "__main__":
    unittest.main()
