"""Hub behaviour: merging reports from parallel workers and building the state document."""

import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

from pihub import config
from pihub.hub import Hub, state_view
from pihub.integrations import KINDS, Integration, Report
from tests.test_integrations import FakeApps


class HubPollTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeApps)
        cls.port = str(cls.server.server_address[1])
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def hub(self, services):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "config.json")
        config.save(path, config.normalise({"services": services}))
        hub = Hub(path)
        self.addCleanup(hub.pool.shutdown)
        return hub

    def test_reports_from_parallel_download_clients_are_merged(self):
        hub = self.hub(
            [
                {"name": "qBittorrent", "kind": "qbittorrent", "port": self.port},
                {"name": "SABnzbd", "kind": "sabnzbd", "port": self.port, "apiKey": "sabkey"},
            ]
        )
        for _ in range(3):  # repeat: a lost update would show up as a wrong total
            hub.poll_once()
            state = hub.state()
            self.assertEqual(state["downloads"]["speed"], "18.4 MB/s")
            self.assertEqual(state["downloads"]["total"], 3)
        pcts = [d["pct"] for d in state["downloads"]["items"]]
        self.assertEqual(pcts, sorted(pcts, reverse=True))
        self.assertIsNone(state["playing"])  # no service provides "playing"

    def test_crashing_integration_does_not_break_the_poll(self):
        class Broken(Integration):
            def stats(self, client, info):
                raise RuntimeError("bug in an integration")

        KINDS["broken-test"] = Broken()
        self.addCleanup(KINDS.pop, "broken-test")
        hub = self.hub(
            [
                {"name": "Broken", "kind": "broken-test", "port": self.port},
                {"name": "Radarr", "kind": "radarr", "port": self.port, "apiKey": "k"},
            ]
        )
        hub.poll_once()
        services = {s["id"]: s for s in hub.state()["services"]}
        self.assertTrue(services["broken"]["up"])
        self.assertRegex(services["broken"]["stat"], r"^Online · \d+ ms$")
        self.assertEqual(services["radarr"]["stat"], "3 queued · 9 wanted")

    def test_scheduled_service_uses_local_status(self):
        hub = self.hub([{"name": "Kometa", "kind": "kometa", "log": "/nonexistent/meta.log"}])
        hub.poll_once()
        kometa = hub.state()["services"][0]
        self.assertEqual((kometa["state"], kometa["stat"], kometa["cron"]), ("Scheduled", "Log not found", True))


class StateViewTests(unittest.TestCase):
    def test_sidebar_sections_follow_provides(self):
        def sections(*kinds):
            cfg = config.normalise({"services": [{"name": k, "kind": k, "port": "1"} for k in kinds]})
            doc = state_view(cfg, [], sys_stats={}, playing=[], downloads=[], download_speed=0)
            return doc["playing"] is not None, doc["downloads"] is not None

        self.assertEqual(sections("generic"), (False, False))
        self.assertEqual(sections("plex"), (True, False))
        self.assertEqual(sections("sabnzbd", "radarr"), (False, True))

    def test_a_new_kind_gets_sidebar_without_hub_changes(self):
        class Jellyfin(Integration):
            provides = frozenset({"playing"})

        KINDS["jellyfin-test"] = Jellyfin()
        self.addCleanup(KINDS.pop, "jellyfin-test")
        cfg = config.normalise({"services": [{"name": "J", "kind": "jellyfin-test", "port": "1"}]})
        self.assertEqual(state_view(cfg, [], sys_stats={}, playing=[], downloads=[], download_speed=0)["playing"], [])

    def test_report_defaults(self):
        self.assertEqual(Report(), Report(status=None, playing=[], downloads=[], download_speed=0.0))


if __name__ == "__main__":
    unittest.main()
