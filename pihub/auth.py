"""Sign-in: password hashing, the admin account, sessions and first-run setup.

Passwords are stored as ``pbkdf2_sha256$<iterations>$<salt>$<hash>`` (salt and
hash base64-encoded), so the iteration count can be raised later without
breaking existing hashes.
"""

import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import time
from collections.abc import Callable, Mapping

from .config import resolve_secret

logger = logging.getLogger(__name__)

PBKDF2_ITERATIONS = 600_000
_SCHEME = "pbkdf2_sha256"
_OFF_VALUES = ("off", "0", "false", "no")


class AuthError(ValueError):
    """A sign-in problem; the message is safe to show users.

    ``status`` is the HTTP status the server answers with.
    """

    status = 400

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        if status is not None:
            self.status = status


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


class Auth:
    """The admin account and its sessions.

    ``mode`` is ``"off"`` (no sign-in), ``"env"`` (account fixed by
    PIHUB_USERNAME/PIHUB_PASSWORD) or ``"file"`` (account stored in ``path``,
    created in the browser on first run).
    """

    def __init__(
        self,
        mode: str = "off",
        *,
        username: str = "",
        password_hash: str = "",
        path: str | None = None,
        setup_code: str = "",
        iterations: int = PBKDF2_ITERATIONS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.mode = mode
        self.username = username
        self.password_hash = password_hash
        self.path = path
        self.setup_code = setup_code
        self.iterations = iterations
        self.clock = clock

    @property
    def enabled(self) -> bool:
        """True unless sign-in is turned off."""
        return self.mode != "off"

    @property
    def needs_setup(self) -> bool:
        """True in file mode until the admin account has been created."""
        return self.mode == "file" and not self.username


def _load_account(path: str) -> tuple[str, str]:
    """Reads ``(username, password_hash)`` from an auth file, or raises AuthError if it is unusable."""
    try:
        with open(path) as f:
            data = json.load(f)
        username, password_hash = data["username"], data["password"]
    except (OSError, ValueError, KeyError, TypeError):
        username = password_hash = None
    if not (isinstance(username, str) and username and isinstance(password_hash, str) and password_hash):
        raise AuthError(f"{path} is unreadable or corrupt; fix or delete it to set up the account again")
    return username, password_hash


def auth_from_env(
    environ: Mapping[str, str], config_path: str | None, *, demo: bool = False, iterations: int = PBKDF2_ITERATIONS
) -> Auth:
    """Builds the Auth for this process from the environment.

    Raises:
        AuthError: The environment or the auth file is inconsistent; pi-hub
            should refuse to start rather than run unprotected.
    """
    if demo:
        return Auth("off")
    if environ.get("PIHUB_AUTH", "").strip().lower() in _OFF_VALUES:
        logger.warning("Sign-in is turned off (PIHUB_AUTH=off): anyone who can reach pi-hub can use it.")
        return Auth("off")
    username = environ.get("PIHUB_USERNAME", "").strip()
    raw_password = environ.get("PIHUB_PASSWORD", "")
    if username and raw_password:
        password = resolve_secret(raw_password)
        if not password:
            raise AuthError("PIHUB_PASSWORD points at an empty or missing variable or file")
        return Auth(
            "env",
            username=username,
            password_hash=hash_password(password, iterations=iterations),
            iterations=iterations,
        )
    if username or raw_password:
        raise AuthError("Set both PIHUB_USERNAME and PIHUB_PASSWORD, or neither to create the account in the browser")
    path = environ.get("PIHUB_AUTH_FILE") or os.path.join(os.path.dirname(config_path or "."), "auth.json")
    if os.path.exists(path):
        username, password_hash = _load_account(path)
        return Auth("file", username=username, password_hash=password_hash, path=path, iterations=iterations)
    return Auth("file", path=path, iterations=iterations)
