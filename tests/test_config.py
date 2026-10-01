import os
import tempfile
import unittest

from pihub import config


def base(**services):
    return config.normalise(
        {"services": [{"name": "Radarr", "kind": "radarr", "port": "7878", "apiKey": "secret1", **services}]}
    )


class NormaliseTests(unittest.TestCase):
    def test_defaults_are_filled(self):
        svc = base()["services"][0]
        self.assertEqual(svc["id"], "radarr")
        self.assertEqual(svc["host"], "127.0.0.1")
        self.assertEqual(svc["scheme"], "http")
        self.assertEqual(svc["category"], "System")  # unknown/missing category -> last one
        self.assertEqual(svc["icon"], "R")

    def test_duplicate_ids_get_suffixes(self):
        cfg = config.normalise({"services": [{"name": "A", "port": "1"}, {"name": "a", "port": "2"}]})
        self.assertEqual([s["id"] for s in cfg["services"]], ["a", "a-2"])

    def test_rejects_bad_values(self):
        bad = [
            {"name": "X", "port": "abc"},
            {"name": "X", "port": "70000"},
            {"name": "X", "kind": "nope", "port": "1"},
            {"name": "X", "port": "1", "host": "evil.com/path"},
            {"name": "X", "port": "1", "scheme": "ftp"},
            {"name": "X", "port": "1", "url": "javascript:alert(1)"},
            {"name": "X", "port": "1", "linkPath": "/a b"},
            {"name": "Radarr", "kind": "radarr"},  # radarr needs a port
            {"port": "1"},
        ]
        for svc in bad:
            with self.subTest(svc=svc), self.assertRaises(config.ConfigError):
                config.normalise({"services": [svc]})

    def test_paths_are_normalised(self):
        svc = config.normalise({"services": [{"name": "P", "port": "1", "linkPath": "web/", "basePath": "/radarr/"}]})
        self.assertEqual(svc["services"][0]["linkPath"], "/web")
        self.assertEqual(svc["services"][0]["basePath"], "/radarr")

    def test_kometa_needs_no_port(self):
        cfg = config.normalise({"services": [{"name": "Kometa", "kind": "kometa", "log": "/logs/meta.log"}]})
        self.assertEqual(cfg["services"][0]["log"], "/logs/meta.log")

    def test_poll_seconds(self):
        self.assertEqual(config.normalise({"pollSeconds": "30"})["pollSeconds"], 30)
        self.assertEqual(config.normalise({"pollSeconds": 1})["pollSeconds"], 5)
        for bad in ("fast", None, [15]):
            with self.subTest(bad=bad), self.assertRaisesRegex(config.ConfigError, "pollSeconds"):
                config.normalise({"pollSeconds": bad})

    def test_schedule_validation(self):
        cfg = config.normalise({"schedule": [{"name": "Backup", "at": "05:00"}]})
        self.assertEqual(cfg["schedule"][0]["at"], ["05:00"])
        with self.assertRaises(config.ConfigError):
            config.normalise({"schedule": [{"name": "Backup", "at": "25:00"}]})


class SecretTests(unittest.TestCase):
    def test_env_and_file_references(self):
        os.environ["PIHUB_TEST_KEY"] = "from-env"
        self.assertEqual(config.resolve_secret("env:PIHUB_TEST_KEY"), "from-env")
        self.assertEqual(config.resolve_secret("env:PIHUB_MISSING_VAR"), "")
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("from-file\n")
        self.addCleanup(os.unlink, f.name)
        self.assertEqual(config.resolve_secret(f"file:{f.name}"), "from-file")
        self.assertEqual(config.resolve_secret("literal"), "literal")

    def test_public_view_never_contains_literal_secrets(self):
        cfg = config.normalise(
            {
                "services": [
                    {"name": "A", "port": "1", "apiKey": "topsecret", "password": "pw"},
                    {"name": "B", "port": "2", "apiKey": "env:B_KEY"},
                ]
            }
        )
        view = config.public_services(cfg)
        self.assertNotIn("topsecret", repr(view))
        self.assertNotIn("'pw'", repr(view))
        self.assertTrue(view[0]["apiKeySet"])
        self.assertEqual(view[1]["apiKeyRef"], "env:B_KEY")


class ApplyUpdateTests(unittest.TestCase):
    def edit(self, cfg, **changes):
        view = config.public_services(cfg)[0]
        return config.apply_update(cfg, {"services": [{**view, **changes}]})

    def test_blank_key_keeps_stored_secret(self):
        new, notices = self.edit(base(), name="Radarr 4K", apiKey="")
        self.assertEqual(new["services"][0]["apiKey"], "secret1")
        self.assertEqual(notices, [])

    def test_new_key_replaces_secret(self):
        new, _ = self.edit(base(), apiKey="secret2")
        self.assertEqual(new["services"][0]["apiKey"], "secret2")

    def test_changing_target_drops_secret(self):
        for change in (
            {"host": "10.0.0.9"},
            {"port": "9999"},
            {"scheme": "https"},
            {"basePath": "/x"},
            {"kind": "sonarr"},
        ):
            with self.subTest(change=change):
                new, notices = self.edit(base(), **change)
                self.assertNotIn("apiKey", new["services"][0])
                self.assertEqual(len(notices), 1)

    def test_changing_target_with_new_key_keeps_new_key(self):
        new, notices = self.edit(base(), host="10.0.0.9", apiKey="secret2")
        self.assertEqual(new["services"][0]["apiKey"], "secret2")
        self.assertEqual(notices, [])

    def test_clear_flag_removes_secret(self):
        new, _ = self.edit(base(), clearApiKey=True)
        self.assertNotIn("apiKey", new["services"][0])

    def test_client_cannot_inject_secret_flags(self):
        new, _ = self.edit(base(), apiKeySet=False, apiKeyRef="env:OTHER")
        self.assertEqual(new["services"][0]["apiKey"], "secret1")

    def test_fields_not_in_ui_are_preserved(self):
        cfg = config.normalise({"services": [{"name": "Kometa", "kind": "kometa", "log": "/l/meta.log"}]})
        view = config.public_services(cfg)[0]
        view.pop("log")
        new, _ = config.apply_update(cfg, {"services": [view]})
        self.assertEqual(new["services"][0]["log"], "/l/meta.log")

    def test_blank_rows_are_dropped_and_new_rows_added(self):
        view = config.public_services(base())[0]
        new, _ = config.apply_update(base(), {"services": [view, {"name": ""}, {"name": "New App", "port": "3000"}]})
        self.assertEqual([s["id"] for s in new["services"]], ["radarr", "new-app"])

    def test_rejects_malformed_payload(self):
        with self.assertRaises(config.ConfigError):
            config.apply_update(base(), {"services": "nope"})


class FileTests(unittest.TestCase):
    def test_save_is_private_and_round_trips(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.json")
            config.save(path, base())
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(config.load(path), base())

    def test_load_errors_are_friendly(self):
        with self.assertRaisesRegex(config.ConfigError, "not found"):
            config.load("/nonexistent/config.json")


class RepoConsistencyTests(unittest.TestCase):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def test_example_config_is_valid(self):
        import json

        with open(os.path.join(self.root, "config.example.json")) as f:
            config.normalise(json.load(f))

    def test_versions_match(self):
        import re

        import pihub

        with open(os.path.join(self.root, "pyproject.toml")) as f:
            self.assertEqual(re.search(r'^version = "([^"]+)"', f.read(), re.M).group(1), pihub.__version__)


if __name__ == "__main__":
    unittest.main()
