import http.client
import json
import os
import tempfile
import threading
import unittest

from pihub import config
from pihub.hostagent import AgentError
from pihub.hub import Hub
from pihub.server import serve


class ServerCase(unittest.TestCase):
    def start(self, password="", readonly=False, agent=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "config.json")
        config.save(
            path,
            config.normalise({"services": [{"name": "Radarr", "kind": "radarr", "port": "1", "apiKey": "topsecret"}]}),
        )
        self.path = path
        httpd = serve(Hub(path), "127.0.0.1", 0, password, readonly, agent)
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


class ServerTests(ServerCase):
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


class FakeAgent:
    """Stands in for AgentClient: records calls, refuses "list" outside the root."""

    def __init__(self):
        self.calls = []

    def call(self, action, **params):
        self.calls.append((action, params))
        if action == "list" and params.get("path") == "..":
            raise AgentError("That folder is outside the allowed root")
        return {"action": action, **params}


JSON = {"Content-Type": "application/json"}


class HostEndpointTests(ServerCase):
    def test_without_agent_host_endpoints_are_absent(self):
        self.start()
        self.assertFalse(json.loads(self.call("GET", "/api/state")[1])["hostAgent"])
        self.assertEqual(self.call("GET", "/api/host")[0], 404)
        self.assertEqual(self.call("POST", "/api/host/reboot", {}, JSON)[0], 404)

    def test_forwards_to_agent(self):
        agent = FakeAgent()
        self.start(agent=agent)
        self.assertTrue(json.loads(self.call("GET", "/api/state")[1])["hostAgent"])
        self.assertEqual(self.call("GET", "/api/host")[0], 200)
        self.assertEqual(self.call("GET", "/api/host/dirs?path=projects%2Fapp")[0], 200)
        status, data, _ = self.call("GET", "/api/host/dirs?path=..")
        self.assertEqual((status, json.loads(data)["error"]), (400, "That folder is outside the allowed root"))
        self.call("POST", "/api/host/claude", {"path": "projects", "name": "x", "extra": "dropped"}, JSON)
        self.call("POST", "/api/host/stop", {"id": "abc"}, JSON)
        self.call("POST", "/api/host/reboot", {}, JSON)
        self.assertEqual(
            agent.calls,
            [
                ("status", {}),
                ("list", {"path": "projects/app"}),
                ("list", {"path": ".."}),
                ("start", {"path": "projects", "name": "x"}),
                ("stop", {"id": "abc"}),
                ("reboot", {}),
            ],
        )

    def test_host_posts_need_json_password_and_writable(self):
        agent = FakeAgent()
        self.start(password="hunter2", agent=agent)
        self.assertEqual(self.call("GET", "/api/host")[0], 401)
        self.assertEqual(self.call("POST", "/api/host/reboot", {}, JSON)[0], 401)
        auth = {"Authorization": "Bearer hunter2"}
        self.assertEqual(self.call("POST", "/api/host/reboot", {}, {**auth, "Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.call("POST", "/api/host/reboot", [], {**auth, **JSON})[0], 400)
        self.assertEqual(agent.calls, [])
        self.assertEqual(self.call("POST", "/api/host/reboot", {}, {**auth, **JSON})[0], 200)

    def test_readonly_blocks_host_actions(self):
        agent = FakeAgent()
        self.start(readonly=True, agent=agent)
        self.assertEqual(self.call("POST", "/api/host/reboot", {}, JSON)[0], 403)
        self.assertEqual(agent.calls, [])


if __name__ == "__main__":
    unittest.main()
