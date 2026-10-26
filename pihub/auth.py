"""Sign-in: password hashing, the admin account, sessions and first-run setup.

Passwords are stored as ``pbkdf2_sha256$<iterations>$<salt>$<hash>`` (salt and
hash base64-encoded), so the iteration count can be raised later without
breaking existing hashes.
"""

import base64
import binascii
import hashlib
import hmac
import os

PBKDF2_ITERATIONS = 600_000
_SCHEME = "pbkdf2_sha256"


def hash_password(password: str, *, salt: bytes | None = None, iterations: int = PBKDF2_ITERATIONS) -> str:
    """Returns a salted PBKDF2-SHA256 hash of ``password`` in the stored format."""
    salt = os.urandom(16) if salt is None else salt
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"{_SCHEME}${iterations}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    """True if ``password`` matches ``stored``; a malformed ``stored`` never matches."""
    try:
        scheme, iterations, salt, digest = stored.split("$")
        if scheme != _SCHEME:
            return False
        raw_salt = base64.b64decode(salt, validate=True)
        expected = base64.b64decode(digest, validate=True)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), raw_salt, int(iterations))
    except (ValueError, binascii.Error):
        return False
    return hmac.compare_digest(actual, expected)
