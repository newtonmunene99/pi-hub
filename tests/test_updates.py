import copy
import json
import os
import stat
import tempfile
import unittest
from unittest import mock

from pihub import updates
from pihub.updates import Updates, UpdatesError

# Two webhook bodies Diun v4.33.1 sent on a Raspberry Pi (status "new", its
# first sighting). Diun documents "update" for a changed digest, so tests set it.
with open(os.path.join(os.path.dirname(__file__), "fixtures", "diun_webhook.json")) as f:
    FIXTURES = json.load(f)


def payload(i=0, **changes):
    body = copy.deepcopy(FIXTURES[i])
    body.update(changes)
    return body


class Clock:
    """A settable stand-in for time.time."""

    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


class StoreTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "updates.json")
        self.clock = Clock()

    def store(self):
        return Updates(self.path, clock=self.clock)

    def test_only_update_status_is_stored(self):
        u = self.store()
        self.assertFalse(u.record(payload(0)))
        self.assertEqual(u.entries(), [])
        self.assertTrue(u.record(payload(0, status="update")))
        [e] = u.entries()
        self.assertEqual(
            e,
            {
                "image": "ghcr.io/flaresolverr/flaresolverr:latest",
                "digest": "sha256:c80ae007ce2ccdcd217a12426e4f039ef763ff90738c808d38810c3e59323767",
                "hubLink": "https://github.com/FlareSolverr/FlareSolverr",
                "created": "2026-09-12T01:28:48.871521329Z",
                "containers": ["flaresolverr"],
                "received": 1_000_000.0,
            },
        )

    def test_malformed_payloads(self):
        for bad in ([], "x", {"status": "update"}, {"status": "update", "image": 1, "digest": "x"}):
            with self.subTest(bad=bad), self.assertRaises(UpdatesError):
                self.store().record(bad)

    def test_same_digest_keeps_first_received_new_digest_replaces(self):
        u = self.store()
        u.record(payload(0, status="update"))
        self.clock.now += 60
        u.record(payload(0, status="update"))
        self.assertEqual(u.entries()[0]["received"], 1_000_000.0)
        u.record(payload(0, status="update", digest="sha256:" + "1" * 64, metadata={"ctn_names": "flare2"}))
        [e] = u.entries()
        self.assertEqual((e["received"], e["digest"], e["containers"]), (1_000_060.0, "sha256:" + "1" * 64, ["flare2"]))

    def test_containers_merge_for_same_image(self):
        u = self.store()
        u.record(payload(0, status="update"))
        u.record(payload(0, status="update", metadata={"ctn_names": " flare2 , ,flaresolverr"}))
        self.assertEqual(u.entries()[0]["containers"], ["flare2", "flaresolverr"])

    def test_missing_metadata_gives_no_containers(self):
        u = self.store()
        u.record({"status": "update", "image": "nginx", "digest": "sha256:1", "metadata": "nope"})
        self.assertEqual((u.entries()[0]["containers"], u.entries()[0]["hubLink"]), ([], ""))

    def test_non_http_hub_link_dropped(self):
        u = self.store()
        u.record(payload(0, status="update", hub_link="javascript:alert(1)"))
        self.assertEqual(u.entries()[0]["hubLink"], "")

    def test_persists_and_survives_corruption(self):
        self.store().record(payload(1, status="update"))
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual([e["image"] for e in self.store().entries()], ["ghcr.io/dictionarry-hub/profilarr:latest"])
        with open(self.path, "w") as f:
            f.write("{oops")
        self.assertEqual(self.store().entries(), [])
        with open(self.path, "w") as f:
            f.write('{"x": 1, "y": {"image": 2}}')
        self.assertEqual(self.store().entries(), [])

    def test_missing_file_starts_empty_and_memory_store_never_saves(self):
        self.assertEqual(self.store().entries(), [])
        u = Updates(clock=self.clock)
        u.record(payload(0, status="update"))
        self.assertEqual(len(u.entries()), 1)
        self.assertFalse(os.path.exists(self.path))

    def test_entries_newest_first(self):
        u = self.store()
        u.record(payload(0, status="update"))
        self.clock.now += 60
        u.record(payload(1, status="update"))
        self.assertEqual([e["containers"] for e in u.entries()], [["profilarr"], ["flaresolverr"]])

    def test_cap_drops_oldest(self):
        u = self.store()
        with mock.patch.object(updates, "MAX_ENTRIES", 3):
            for n in range(4):
                self.clock.now += 1
                u.record(payload(0, status="update", image=f"app{n}"))
        self.assertEqual([e["image"] for e in u.entries()], ["app3", "app2", "app1"])


if __name__ == "__main__":
    unittest.main()
