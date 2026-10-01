"""Integrations against a fake HTTP server that mimics each app's API."""

import json
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

from pihub.integrations import KINDS, Client, Kometa, Report, is_scheduled, kinds_info

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
        self.assertEqual(KINDS["radarr"].stats(self.client("radarr", apiKey="k"), None), "3 queued · 9 wanted")
        self.assertEqual(KINDS["sonarr"].stats(self.client("sonarr", apiKey="k"), None), "3 queued · 9 missing")
        self.assertEqual(KINDS["radarr"].info(self.client("radarr", apiKey="k"))["version"], "6.4.4")
        sent = [h for p, h in FakeApps.requests if p == "/api/v3/queue"][-1]
        self.assertEqual(sent.get("X-Api-Key"), "k")

    def test_no_key_means_no_stats_and_no_request(self):
        before = len(FakeApps.requests)
        self.assertIsNone(KINDS["radarr"].stats(self.client("radarr"), None))
        self.assertEqual(len(FakeApps.requests), before)

    def test_prowlarr_counts_failing_indexers(self):
        self.assertEqual(KINDS["prowlarr"].stats(self.client("prowlarr", apiKey="k"), None), "1/2 indexers healthy")

    def test_bazarr(self):
        self.assertEqual(
            KINDS["bazarr"].stats(self.client("bazarr", apiKey="k"), None), "1,234 episodes · 5 movies wanted"
        )

    def test_qbittorrent_reports_downloads(self):
        report = KINDS["qbittorrent"].stats(self.client("qbittorrent"), None)
        self.assertIsInstance(report, Report)
        self.assertEqual(report.status, "↓ 14.2 MB/s · 1 active · 1 queued")
        self.assertEqual(len(report.downloads), 2)
        self.assertEqual(report.download_speed, 14_200_000)

    def test_sabnzbd(self):
        report = KINDS["sabnzbd"].stats(self.client("sabnzbd", apiKey="sabkey"), None)
        self.assertEqual(report.status, "↓ 4.2 MB/s · 1 active")
        self.assertEqual(report.downloads[0]["name"], "Some.Show")
        self.assertIsNone(KINDS["sabnzbd"].stats(self.client("sabnzbd", apiKey="wrong"), None))

    def test_plex_sessions(self):
        report = KINDS["plex"].stats(self.client("plex", apiKey="tok"), {"extra": {"titles": 4812}})
        self.assertEqual(report.status, "1 stream · 4,812 titles")
        self.assertEqual(report.playing[0]["title"], "Andor S02E09")
        self.assertEqual(report.playing[0]["how"], "Transcode 1080p")
        self.assertEqual(report.playing[0]["pct"], 25)

    def test_secret_resolver_is_used(self):
        c = Client(
            {"id": "r", "port": self.port, "apiKey": "env:X"}, secret=lambda v: "resolved" if v == "env:X" else v
        )
        self.assertEqual(c.api_key, "resolved")

    def test_unreachable_service(self):
        c = Client({"id": "x", "port": "1", "host": "127.0.0.1"}, timeout=1)
        self.assertEqual(c.request("/"), (None, None, None))


class RegistryTests(unittest.TestCase):
    def test_scheduled_only_for_portless_local_kinds(self):
        self.assertTrue(is_scheduled({"kind": "kometa", "port": ""}))
        self.assertFalse(is_scheduled({"kind": "kometa", "port": "1234"}))
        self.assertFalse(is_scheduled({"kind": "generic", "port": ""}))

    def test_kinds_info_covers_every_kind(self):
        info = kinds_info()
        self.assertEqual(set(info), set(KINDS))
        self.assertEqual(info["radarr"]["defaultPort"], "7878")


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
        self.assertEqual(Kometa().local_status({"log": "/nonexistent/meta.log"}), ("Log not found", False))


class FakeQBittorrent(BaseHTTPRequestHandler):
    """Cookie-authenticated like the real Web API: login gives a SID, else 403."""

    password = "right"
    logins: ClassVar[list] = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        form = urllib.parse.parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
        FakeQBittorrent.logins.append(form["password"][0])
        ok = form["password"] == [self.password]
        self.send_response(200)
        if ok:
            self.send_header("Set-Cookie", "SID=abc; HttpOnly; path=/")
        self.end_headers()
        self.wfile.write(b"Ok." if ok else b"Fails.")

    def do_GET(self):
        if "SID=abc" not in self.headers.get("Cookie", ""):
            self.send_response(403)
            self.end_headers()
            return
        body = {"/api/v2/transfer/info": {"dl_info_speed": 0}, "/api/v2/app/version": None}
        path = urllib.parse.urlparse(self.path).path
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"v5.1.0" if path == "/api/v2/app/version" else json.dumps(body.get(path, [])).encode())


class QBittorrentLoginTests(unittest.TestCase):
    def setUp(self):
        FakeQBittorrent.logins = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeQBittorrent)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def client(self, password):
        port = str(self.server.server_address[1])
        return Client({"id": "q", "kind": "qbittorrent", "port": port, "username": "admin", "password": password})

    def test_correct_password_logs_in_once(self):
        c, qbit = self.client("right"), KINDS["qbittorrent"]
        for _ in range(3):
            self.assertEqual(qbit.stats(c, None).status, "↓ 0 MB/s · 0 active")
        self.assertEqual(FakeQBittorrent.logins, ["right"])
        self.assertEqual(qbit.info(c)["version"], "5.1.0")

    def test_wrong_password_is_not_retried(self):
        # qBittorrent bans an IP after 5 failed logins; pi-hub must not get there.
        c, qbit = self.client("wrong"), KINDS["qbittorrent"]
        for _ in range(10):
            self.assertEqual(qbit.stats(c, None), qbit.LOGIN_FAILED)
        qbit.info(c)
        self.assertEqual(len(FakeQBittorrent.logins), 1)

    def test_new_credentials_get_a_fresh_attempt(self):
        qbit = KINDS["qbittorrent"]
        qbit.stats(self.client("wrong"), None)
        self.assertEqual(qbit.stats(self.client("right"), None).status, "↓ 0 MB/s · 0 active")
        self.assertEqual(FakeQBittorrent.logins, ["wrong", "right"])


if __name__ == "__main__":
    unittest.main()
