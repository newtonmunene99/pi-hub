import http.client
import json
import os
import tempfile
import threading
import unittest

from pihub import config
from pihub.auth import MAX_FAILURES, Auth, hash_password
from pihub.hostagent import AgentError
from pihub.hub import Hub
from pihub.server import serve


class ServerCase(unittest.TestCase):
    def start(self, auth=None, readonly=False, agent=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "config.json")
        config.save(
            path,
            config.normalise({"services": [{"name": "Radarr", "kind": "radarr", "port": "1", "apiKey": "topsecret"}]}),
        )
        self.path = path
        httpd = serve(Hub(path), "127.0.0.1", 0, auth, readonly, agent)
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

    def login(self, pw="s3cretpass"):
        """Signs in as admin; returns the status and headers carrying the session cookie."""
        status, _, resp = self.call("POST", "/api/login", {"username": "admin", "password": pw}, JSON)
        cookie = resp.getheader("Set-Cookie") or ""
        return status, ({"Cookie": cookie.split(";")[0]} if cookie else {})


JSON = {"Content-Type": "application/json"}


def env_auth():
    return Auth("env", username="admin", password_hash=hash_password("s3cretpass", iterations=1000), iterations=1000)


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

    def test_host_posts_need_json_sign_in_and_writable(self):
        agent = FakeAgent()
        self.start(auth=env_auth(), agent=agent)
        self.assertEqual(self.call("GET", "/api/host")[0], 401)
        self.assertEqual(self.call("POST", "/api/host/reboot", {}, JSON)[0], 401)
        _, auth = self.login()
        self.assertEqual(self.call("POST", "/api/host/reboot", {}, {**auth, "Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.call("POST", "/api/host/reboot", [], {**auth, **JSON})[0], 400)
        self.assertEqual(agent.calls, [])
        self.assertEqual(self.call("POST", "/api/host/reboot", {}, {**auth, **JSON})[0], 200)

    def test_readonly_blocks_host_actions(self):
        agent = FakeAgent()
        self.start(readonly=True, agent=agent)
        self.assertEqual(self.call("POST", "/api/host/reboot", {}, JSON)[0], 403)
        self.assertEqual(agent.calls, [])


class SignInTests(ServerCase):
    def test_everything_but_public_endpoints_needs_a_session(self):
        self.start(auth=env_auth(), agent=FakeAgent())
        for path in ("/api/state", "/api/config", "/api/host", "/api/host/dirs", "/api/nope"):
            with self.subTest(path=path):
                status, data, _ = self.call("GET", path)
                self.assertEqual((status, json.loads(data)["error"]), (401, "Sign in required"))
        self.assertEqual(self.call("POST", "/api/config", {"services": []}, JSON)[0], 401)
        self.assertEqual(self.call("POST", "/api/host/reboot", {}, JSON)[0], 401)
        self.assertEqual(self.call("GET", "/api/health")[0], 200)
        self.assertEqual(self.call("GET", "/")[0], 200)
        self.assertEqual(self.call("GET", "/app.js")[0], 200)
        self.assertEqual(
            json.loads(self.call("GET", "/api/auth")[1]),
            {"enabled": True, "authenticated": False, "username": "", "setup": False, "canChangePassword": False},
        )

    def test_login_sets_httponly_cookie(self):
        self.start(auth=env_auth())
        status, data, resp = self.call("POST", "/api/login", {"username": "admin", "password": "s3cretpass"}, JSON)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["username"], "admin")
        cookie = resp.getheader("Set-Cookie")
        for part in ("pihub_session=", "HttpOnly", "SameSite=Strict", "Path=/", "Max-Age=2592000"):
            self.assertIn(part, cookie)
        self.assertNotIn(b"s3cretpass", data)
        headers = {"Cookie": cookie.split(";")[0]}
        for path in ("/api/state", "/api/config"):
            with self.subTest(path=path):
                self.assertEqual(self.call("GET", path, headers=headers)[0], 200)
        self.assertEqual(json.loads(self.call("GET", "/api/auth", headers=headers)[1])["username"], "admin")
        self.assertNotIn("locked", json.loads(self.call("GET", "/api/state", headers=headers)[1]))

    def test_wrong_password(self):
        self.start(auth=env_auth())
        status, data, resp = self.call("POST", "/api/login", {"username": "admin", "password": "nope"}, JSON)
        self.assertEqual((status, json.loads(data)["error"]), (401, "Wrong username or password"))
        self.assertIsNone(resp.getheader("Set-Cookie"))

    def test_login_needs_json_object(self):
        self.start(auth=env_auth())
        body = {"username": "admin", "password": "s3cretpass"}
        self.assertEqual(self.call("POST", "/api/login", body, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.call("POST", "/api/login", ["admin"], JSON)[0], 400)

    def test_logout_clears_session(self):
        self.start(auth=env_auth())
        _, headers = self.login()
        status, _, resp = self.call("POST", "/api/logout", {}, {**headers, **JSON})
        self.assertEqual(status, 200)
        self.assertIn("Max-Age=0", resp.getheader("Set-Cookie"))
        self.assertEqual(self.call("GET", "/api/state", headers=headers)[0], 401)

    def test_throttled_login_sends_retry_after(self):
        self.start(auth=env_auth())
        for _ in range(5):
            self.login("nope")
        status, _, resp = self.call("POST", "/api/login", {"username": "admin", "password": "s3cretpass"}, JSON)
        self.assertEqual((status, resp.getheader("Retry-After")), (429, "60"))
        self.assertEqual(MAX_FAILURES, 5)

    def test_garbage_cookie_is_not_a_session(self):
        self.start(auth=env_auth())
        for cookie in ('pihub_session="x; ;;', "pihub_session=forged", "other=1"):
            with self.subTest(cookie=cookie):
                self.assertEqual(self.call("GET", "/api/state", headers={"Cookie": cookie})[0], 401)

    def test_readonly_still_allows_sign_in(self):
        self.start(auth=env_auth(), readonly=True)
        self.assertEqual(self.login()[0], 200)

    def test_auth_off_serves_everything(self):
        self.start()
        self.assertEqual(self.call("GET", "/api/state")[0], 200)
        self.assertEqual(self.call("GET", "/api/config")[0], 200)
        self.assertEqual(json.loads(self.call("GET", "/api/auth")[1])["enabled"], False)


class SetupEndpointTests(ServerCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.auth_path = os.path.join(tmp.name, "auth.json")

    def test_first_run(self):
        self.start(auth=Auth("file", path=self.auth_path, setup_code="abcd1234", iterations=1000))
        self.assertTrue(json.loads(self.call("GET", "/api/auth")[1])["setup"])
        body = {"code": "wrong", "username": "admin", "password": "s3cretpass"}
        status, data, _ = self.call("POST", "/api/setup", body, JSON)
        self.assertEqual((status, json.loads(data)["error"]), (403, "Wrong setup code"))
        status, data, resp = self.call("POST", "/api/setup", {**body, "code": "abcd1234"}, JSON)
        self.assertEqual(status, 200, data)
        self.assertEqual(json.loads(data)["username"], "admin")
        self.assertIn("HttpOnly", resp.getheader("Set-Cookie"))
        headers = {"Cookie": resp.getheader("Set-Cookie").split(";")[0]}
        self.assertEqual(self.call("GET", "/api/state", headers=headers)[0], 200)
        self.assertEqual(os.stat(self.auth_path).st_mode & 0o777, 0o600)
        self.assertEqual(self.call("POST", "/api/setup", {**body, "code": "abcd1234"}, JSON)[0], 409)

    def test_setup_needs_json(self):
        self.start(auth=Auth("file", path=self.auth_path, setup_code="abcd1234", iterations=1000))
        body = {"code": "abcd1234", "username": "admin", "password": "s3cretpass"}
        self.assertEqual(self.call("POST", "/api/setup", body, {"Content-Type": "text/plain"})[0], 415)
        self.assertFalse(os.path.exists(self.auth_path))

    def test_setup_refused_in_env_mode(self):
        self.start(auth=env_auth())
        body = {"code": "x", "username": "admin", "password": "s3cretpass"}
        self.assertEqual(self.call("POST", "/api/setup", body, JSON)[0], 409)


class PasswordEndpointTests(ServerCase):
    def test_change_password(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        a = Auth("file", path=os.path.join(tmp.name, "auth.json"), setup_code="abcd1234", iterations=1000)
        a.setup("abcd1234", "admin", "s3cretpass", "127.0.0.1")
        self.start(auth=a, readonly=True)
        _, mine = self.login()
        _, other = self.login()
        good = {"current": "s3cretpass", "new": "n3wpassword"}
        self.assertEqual(self.call("POST", "/api/password", good, JSON)[0], 401)
        bad = {"current": "nope", "new": "n3wpassword"}
        status, data, _ = self.call("POST", "/api/password", bad, {**mine, **JSON})
        self.assertEqual((status, json.loads(data)["error"]), (403, "Current password is wrong"))
        self.assertEqual(self.call("POST", "/api/password", good, {**mine, "Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.call("POST", "/api/password", good, {**mine, **JSON})[0], 200)
        self.assertEqual(self.call("GET", "/api/state", headers=mine)[0], 200)
        self.assertEqual(self.call("GET", "/api/state", headers=other)[0], 401)
        self.assertEqual(self.login("s3cretpass")[0], 401)
        self.assertEqual(self.login("n3wpassword")[0], 200)

    def test_env_mode_refuses(self):
        self.start(auth=env_auth())
        _, headers = self.login()
        body = {"current": "s3cretpass", "new": "n3wpassword"}
        status, data, _ = self.call("POST", "/api/password", body, {**headers, **JSON})
        self.assertEqual((status, json.loads(data)["error"]), (409, "The password is set by PIHUB_PASSWORD"))


if __name__ == "__main__":
    unittest.main()
