import json
import os
import tempfile
import unittest

from pihub import auth

FAST = 1000  # PBKDF2 iterations: the real default is too slow for tests
# base64(hashlib.pbkdf2_hmac("sha256", b"pw", b"0123456789abcdef", 1000))
KNOWN_HASH = "g4BiOBrcHGnGTAdCcoA+WrLOFA69L2c9HA5vVpt2o7A="


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


if __name__ == "__main__":
    unittest.main()
