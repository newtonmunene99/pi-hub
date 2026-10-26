import json
import os
import re
import tempfile
import unittest

from pihub import auth

FAST = 1000  # PBKDF2 iterations: the real default is too slow for tests
# base64(hashlib.pbkdf2_hmac("sha256", b"pw", b"0123456789abcdef", 1000))
KNOWN_HASH = "g4BiOBrcHGnGTAdCcoA+WrLOFA69L2c9HA5vVpt2o7A="


class Clock:
    """A settable stand-in for time.time."""

    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def env_auth(clock=None):
    return auth.Auth(
        "env",
        username="admin",
        password_hash=auth.hash_password("s3cretpass", iterations=FAST),
        iterations=FAST,
        clock=clock or Clock(),
    )


class HashTests(unittest.TestCase):
    def test_round_trip(self):
        stored = auth.hash_password("correct horse", iterations=FAST)
        self.assertTrue(stored.startswith("pbkdf2_sha256$1000$"))
        self.assertTrue(auth.verify_password("correct horse", stored))
        self.assertFalse(auth.verify_password("wrong horse", stored))

    def test_salt_makes_hashes_differ(self):
        self.assertNotEqual(auth.hash_password("pw", iterations=FAST), auth.hash_password("pw", iterations=FAST))

    def test_known_vector(self):
        stored = auth.hash_password("pw", salt=b"0123456789abcdef", iterations=FAST)
        self.assertEqual(stored, "pbkdf2_sha256$1000$MDEyMzQ1Njc4OWFiY2RlZg==$" + KNOWN_HASH)

    def test_malformed_stored_value_never_verifies(self):
        for bad in ("", "plain", "md5$1$x$y", "pbkdf2_sha256$notanint$x$y", "pbkdf2_sha256$1000$!!$!!"):
            with self.subTest(bad=bad):
                self.assertFalse(auth.verify_password("pw", bad))

    def test_default_iterations(self):
        self.assertEqual(auth.PBKDF2_ITERATIONS, 600_000)


class ModeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.cfg = os.path.join(self.dir, "config.json")

    def mode(self, env, demo=False):
        return auth.auth_from_env(env, self.cfg, demo=demo, iterations=FAST)

    def write_account(self, path):
        with open(path, "w") as f:
            json.dump({"username": "admin", "password": auth.hash_password("s3cretpass", iterations=FAST)}, f)

    def test_demo_is_off(self):
        self.assertEqual(self.mode({"PIHUB_USERNAME": "a", "PIHUB_PASSWORD": "b"}, demo=True).mode, "off")

    def test_opt_out_logs_warning(self):
        with self.assertLogs("pihub.auth", "WARNING") as logs:
            a = self.mode({"PIHUB_AUTH": "OFF"})
        self.assertEqual((a.mode, a.enabled), ("off", False))
        self.assertIn("PIHUB_AUTH=off", logs.output[0])

    def test_env_credentials(self):
        a = self.mode({"PIHUB_USERNAME": "admin", "PIHUB_PASSWORD": "s3cretpass"})
        self.assertEqual((a.mode, a.enabled, a.needs_setup), ("env", True, False))

    def test_password_file_reference(self):
        secret = os.path.join(self.dir, "pw")
        with open(secret, "w") as f:
            f.write("s3cretpass\n")
        self.assertEqual(self.mode({"PIHUB_USERNAME": "admin", "PIHUB_PASSWORD": f"file:{secret}"}).mode, "env")

    def test_empty_reference_is_an_error(self):
        with self.assertRaisesRegex(auth.AuthError, "empty or missing"):
            self.mode({"PIHUB_USERNAME": "admin", "PIHUB_PASSWORD": "file:/nonexistent/pw"})

    def test_half_set_pair_is_an_error(self):
        for env in ({"PIHUB_PASSWORD": "x"}, {"PIHUB_USERNAME": "admin"}):
            with self.subTest(env=env), self.assertRaisesRegex(auth.AuthError, "PIHUB_USERNAME and PIHUB_PASSWORD"):
                self.mode(env)

    def test_file_mode_without_file_needs_setup(self):
        a = self.mode({})
        self.assertEqual((a.mode, a.enabled, a.needs_setup), ("file", True, True))

    def test_file_mode_loads_account(self):
        self.write_account(os.path.join(self.dir, "auth.json"))
        a = self.mode({})
        self.assertEqual((a.mode, a.needs_setup), ("file", False))

    def test_auth_file_override(self):
        path = os.path.join(self.dir, "elsewhere.json")
        self.write_account(path)
        self.assertFalse(self.mode({"PIHUB_AUTH_FILE": path}).needs_setup)

    def test_corrupt_file_is_an_error_not_setup(self):
        for body in ("{not json", "[]", '{"username": "admin"}', '{"username": "", "password": "x"}'):
            with open(os.path.join(self.dir, "auth.json"), "w") as f:
                f.write(body)
            with self.subTest(body=body), self.assertRaisesRegex(auth.AuthError, "corrupt"):
                self.mode({})


class SessionTests(unittest.TestCase):
    def test_login_and_expiry(self):
        clock = Clock()
        a = env_auth(clock)
        token = a.login("admin", "s3cretpass", "10.0.0.2")
        self.assertEqual(a.session_user(token), "admin")
        clock.now += auth.SESSION_SECONDS - 1
        self.assertEqual(a.session_user(token), "admin")
        clock.now += 2
        self.assertIsNone(a.session_user(token))

    def test_wrong_credentials_same_message(self):
        a = env_auth()
        for user, pw in (("admin", "nope"), ("root", "s3cretpass"), ("", "")):
            with self.subTest(user=user), self.assertRaises(auth.AuthError) as cm:
                a.login(user, pw, "10.0.0.2")
            self.assertEqual((str(cm.exception), cm.exception.status), ("Wrong username or password", 401))

    def test_tokens_are_distinct(self):
        a = env_auth()
        self.assertNotEqual(a.login("admin", "s3cretpass", "10.0.0.2"), a.login("admin", "s3cretpass", "10.0.0.2"))

    def test_logout(self):
        a = env_auth()
        token = a.login("admin", "s3cretpass", "10.0.0.2")
        a.logout(token)
        a.logout(None)
        a.logout("unknown")
        self.assertIsNone(a.session_user(token))

    def test_unknown_token(self):
        a = env_auth()
        self.assertIsNone(a.session_user("forged"))
        self.assertIsNone(a.session_user(None))
        self.assertIsNone(a.session_user(""))

    def test_status(self):
        a = env_auth()
        token = a.login("admin", "s3cretpass", "10.0.0.2")
        self.assertEqual(
            a.status(token),
            {"enabled": True, "authenticated": True, "username": "admin", "setup": False, "canChangePassword": False},
        )
        self.assertEqual(
            a.status(None),
            {"enabled": True, "authenticated": False, "username": "", "setup": False, "canChangePassword": False},
        )

    def test_status_when_off(self):
        self.assertEqual(
            auth.Auth("off").status(None),
            {"enabled": False, "authenticated": True, "username": "", "setup": False, "canChangePassword": False},
        )

    def test_status_in_file_mode(self):
        fresh = auth.Auth("file", path=os.devnull)
        self.assertTrue(fresh.status(None)["setup"])
        stored = auth.Auth(
            "file", username="admin", password_hash=auth.hash_password("s3cretpass", iterations=FAST), path=os.devnull
        )
        token = stored.login("admin", "s3cretpass", "10.0.0.2")
        self.assertEqual(stored.status(token)["canChangePassword"], True)
        self.assertEqual(stored.status(None)["canChangePassword"], False)


class SetupTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cfg = os.path.join(tmp.name, "config.json")
        self.path = os.path.join(tmp.name, "auth.json")

    def fresh(self):
        with self.assertLogs("pihub.auth", "WARNING") as logs:
            a = auth.auth_from_env({}, self.cfg, iterations=FAST)
        code = re.search(r"setup code (\w+)", logs.output[0]).group(1)
        return a, code

    def test_setup_code_is_logged_and_random(self):
        _, first = self.fresh()
        _, second = self.fresh()
        self.assertEqual(len(first), 8)
        self.assertNotEqual(first, second)

    def test_setup_creates_private_file_and_signs_in(self):
        a, code = self.fresh()
        token = a.setup(code, " admin ", "s3cretpass", "10.0.0.2")
        self.assertEqual(a.session_user(token), "admin")
        self.assertFalse(a.needs_setup)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        with open(self.path) as f:
            saved = json.load(f)
        self.assertEqual(saved["username"], "admin")
        self.assertNotIn("s3cretpass", json.dumps(saved))
        reloaded = auth.auth_from_env({}, self.cfg, iterations=FAST)
        self.assertIsInstance(reloaded.login("admin", "s3cretpass", "10.0.0.2"), str)

    def test_wrong_code(self):
        a, _ = self.fresh()
        for code in ("00000000", ""):
            with self.subTest(code=code), self.assertRaises(auth.AuthError) as cm:
                a.setup(code, "admin", "s3cretpass", "10.0.0.2")
            self.assertEqual((str(cm.exception), cm.exception.status), ("Wrong setup code", 403))
        self.assertFalse(os.path.exists(self.path))

    def test_validation(self):
        a, code = self.fresh()
        for user, pw in (("", "s3cretpass"), ("  ", "s3cretpass"), ("admin", "short")):
            with self.subTest(user=user, pw=pw), self.assertRaises(auth.AuthError) as cm:
                a.setup(code, user, pw, "10.0.0.2")
            self.assertEqual(cm.exception.status, 400)
        self.assertTrue(a.needs_setup)

    def test_second_setup_refused(self):
        a, code = self.fresh()
        a.setup(code, "admin", "s3cretpass", "10.0.0.2")
        with self.assertRaises(auth.AuthError) as cm:
            a.setup(code, "evil", "s3cretpass", "10.0.0.3")
        self.assertEqual(cm.exception.status, 409)

    def test_env_mode_has_no_setup(self):
        with self.assertRaises(auth.AuthError) as cm:
            env_auth().setup("x", "admin", "s3cretpass", "10.0.0.2")
        self.assertEqual(cm.exception.status, 409)

    def test_existing_account_logs_no_code(self):
        a, code = self.fresh()
        a.setup(code, "admin", "s3cretpass", "10.0.0.2")
        with self.assertNoLogs("pihub.auth", "WARNING"):
            auth.auth_from_env({}, self.cfg, iterations=FAST)


class ThrottleTests(unittest.TestCase):
    def fail_logins(self, a, client, n):
        for _ in range(n):
            with self.assertRaises(auth.AuthError):
                a.login("admin", "nope", client)

    def test_lockout_and_recovery(self):
        clock = Clock()
        a = env_auth(clock)
        self.fail_logins(a, "10.0.0.2", auth.MAX_FAILURES)
        with self.assertRaises(auth.Throttled) as cm:
            a.login("admin", "s3cretpass", "10.0.0.2")  # right password, still locked
        self.assertEqual((cm.exception.status, cm.exception.retry_after), (429, 60))
        self.assertIsInstance(a.login("admin", "s3cretpass", "10.0.0.9"), str)  # other clients unaffected
        clock.now += auth.LOCKOUT_SECONDS - 0.5
        with self.assertRaises(auth.Throttled) as cm:
            a.login("admin", "s3cretpass", "10.0.0.2")
        self.assertEqual(cm.exception.retry_after, 1)
        clock.now += 0.5
        self.assertIsInstance(a.login("admin", "s3cretpass", "10.0.0.2"), str)

    def test_success_resets_count(self):
        a = env_auth()
        self.fail_logins(a, "10.0.0.2", auth.MAX_FAILURES - 1)
        a.login("admin", "s3cretpass", "10.0.0.2")
        self.fail_logins(a, "10.0.0.2", auth.MAX_FAILURES - 1)
        self.assertIsInstance(a.login("admin", "s3cretpass", "10.0.0.2"), str)

    def test_setup_code_guesses_are_throttled(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "auth.json")
        a = auth.Auth("file", path=path, setup_code="abcd1234", iterations=FAST, clock=Clock())
        for _ in range(auth.MAX_FAILURES):
            with self.assertRaises(auth.AuthError):
                a.setup("wrong", "admin", "s3cretpass", "10.0.0.2")
        with self.assertRaises(auth.Throttled):
            a.setup("abcd1234", "admin", "s3cretpass", "10.0.0.2")
        self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
