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


if __name__ == "__main__":
    unittest.main()
