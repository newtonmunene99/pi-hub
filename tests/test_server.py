import http.client
import json
import os
import tempfile
import threading
import unittest

from pihub import config
from pihub.hub import Hub
from pihub.server import serve


class ServerTests(unittest.TestCase):
    def start(self, password="", readonly=False):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "config.json")
        config.save(
            path,
            config.normalise({"services": [{"name": "Radarr", "kind": "radarr", "port": "1", "apiKey": "topsecret"}]}),
        )
        self.path = path
        httpd = serve(Hub(path), "127.0.0.1", 0, password, readonly)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        self.port = httpd.server_address[1]

    def call(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers or {})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data, resp

    def test_state_and_static(self):
        self.start()
        status, data, resp = self.call("GET", "/api/state")
        self.assertEqual(status, 200)
        state = json.loads(data)
        self.assertEqual(state["services"][0]["name"], "Radarr")
        self.assertNotIn(b"topsecret", data)
        self.assertIn("default-src 'self'", resp.getheader("Content-Security-Policy"))
        status, data, _ = self.call("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"app.js", data)

    def test_path_traversal_blocked(self):
        self.start()
        for path in ("/../config.py", "/%2e%2e/config.py", "/fonts/../../server.py", "/static/../server.py"):
            with self.subTest(path=path):
                self.assertEqual(self.call("GET", path)[0], 404)

    def test_config_never_returns_secret(self):
        self.start()
        status, data, _ = self.call("GET", "/api/config")
        self.assertEqual(status, 200)
        self.assertNotIn(b"topsecret", data)
        self.assertTrue(json.loads(data)["services"][0]["apiKeySet"])

    def test_save_requires_json_content_type(self):
        self.start()
        status, _, _ = self.call("POST", "/api/config", {"services": []}, {"Content-Type": "text/plain"})
        self.assertEqual(status, 415)

    def test_save_round_trip(self):
        self.start()
        _, data, _ = self.call("GET", "/api/config")
        services = json.loads(data)["services"]
        services[0]["name"] = "Radarr HD"
        status, data, _ = self.call("POST", "/api/config", {"services": services}, {"Content-Type": "application/json"})
        self.assertEqual(status, 200, data)
        saved = config.load(self.path)
        self.assertEqual(saved["services"][0]["name"], "Radarr HD")
        self.assertEqual(saved["services"][0]["apiKey"], "topsecret")

    def test_invalid_save_is_rejected(self):
        self.start()
        status, data, _ = self.call(
            "POST", "/api/config", {"services": [{"name": "X", "port": "nope"}]}, {"Content-Type": "application/json"}
        )
        self.assertEqual(status, 400)
        self.assertIn(b"port", data)

    def test_password(self):
        self.start(password="hunter2")
        self.assertEqual(self.call("GET", "/api/config")[0], 401)
        self.assertEqual(self.call("GET", "/api/config", headers={"Authorization": "Bearer wrong"})[0], 401)
        self.assertEqual(self.call("GET", "/api/config", headers={"Authorization": "Bearer hunter2"})[0], 200)
        self.assertEqual(
            self.call("POST", "/api/config", {"services": []}, {"Content-Type": "application/json"})[0], 401
        )
        self.assertEqual(self.call("GET", "/api/state")[0], 200)  # dashboard stays public

    def test_readonly(self):
        self.start(readonly=True)
        status, _, _ = self.call("POST", "/api/config", {"services": []}, {"Content-Type": "application/json"})
        self.assertEqual(status, 403)
        self.assertTrue(json.loads(self.call("GET", "/api/state")[1])["readonly"])


if __name__ == "__main__":
    unittest.main()
