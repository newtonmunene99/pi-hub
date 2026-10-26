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
import math
import os
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

from .config import resolve_secret

logger = logging.getLogger(__name__)

PBKDF2_ITERATIONS = 600_000
SESSION_SECONDS = 30 * 24 * 3600
COOKIE_NAME = "pihub_session"
MIN_PASSWORD_LENGTH = 8
MAX_FAILURES = 5
LOCKOUT_SECONDS = 60
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


class Throttled(AuthError):
    """Too many failed attempts from one client; retry after ``retry_after`` seconds."""

    status = 429

    def __init__(self, retry_after: int) -> None:
        super().__init__("Too many attempts — try again in a minute")
        self.retry_after = retry_after


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
        self._lock = threading.Lock()
        self._sessions: dict[str, tuple[str, float]] = {}  # token -> (username, expires)
        self._failures: dict[str, tuple[int, float]] = {}  # client -> (failures, locked until)

    @property
    def enabled(self) -> bool:
        """True unless sign-in is turned off."""
        return self.mode != "off"

    @property
    def needs_setup(self) -> bool:
        """True in file mode until the admin account has been created."""
        return self.mode == "file" and not self.username

    def status(self, token: str | None) -> dict[str, Any]:
        """What the UI needs to know: whether sign-in is on, and who is signed in."""
        user = self.session_user(token) if self.enabled else None
        return {
            "enabled": self.enabled,
            "authenticated": not self.enabled or user is not None,
            "username": user or "",
            "setup": self.needs_setup,
            "canChangePassword": user is not None and self.mode == "file",
        }

    def login(self, username: str, password: str, client: str) -> str:
        """Checks the credentials and returns a new session token.

        Raises:
            AuthError: Wrong username or password (status 401). The message
                doesn't say which, and the password is always hashed, so
                neither the reply nor its timing reveals the username.
        """
        self._check_throttle(client)
        user_ok = hmac.compare_digest(username.encode(), self.username.encode())
        password_ok = bool(self.password_hash) and verify_password(password, self.password_hash)
        if not (user_ok and password_ok):
            self._record_failure(client)
            raise AuthError("Wrong username or password", 401)
        self._clear_failures(client)
        return self._new_session()

    def session_user(self, token: str | None) -> str | None:
        """The username a live session token belongs to, or None."""
        if not token:
            return None
        now = self.clock()
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return None
            if session[1] <= now:
                del self._sessions[token]
                return None
            return session[0]

    def logout(self, token: str | None) -> None:
        """Ends a session; unknown tokens are ignored."""
        if token:
            with self._lock:
                self._sessions.pop(token, None)

    def setup(self, code: str, username: str, password: str, client: str) -> str:
        """Creates the admin account on first run and returns a session token for it.

        Raises:
            AuthError: 409 once an account exists (or outside file mode), 403
                for a wrong setup code, 400 for an unusable username or password.
        """
        if not self.needs_setup or self.path is None:
            raise AuthError("An account already exists", 409)
        self._check_throttle(client)
        if not (self.setup_code and hmac.compare_digest(code.encode(), self.setup_code.encode())):
            self._record_failure(client)
            raise AuthError("Wrong setup code", 403)
        self._clear_failures(client)
        username = username.strip()
        if not username:
            raise AuthError("Choose a username")
        _check_password(password)
        self.username = username
        self.password_hash = hash_password(password, iterations=self.iterations)
        self._save(self.path)
        self.setup_code = ""
        return self._new_session()

    def change_password(self, token: str | None, current: str, new: str) -> None:
        """Replaces the password and signs out every other session.

        Raises:
            AuthError: 409 in env mode, 401 without a live session, 403 for a
                wrong current password, 400 for a too-short new one.
        """
        if self.mode != "file" or self.path is None:
            raise AuthError("The password is set by PIHUB_PASSWORD", 409)
        if token is None or self.session_user(token) is None:
            raise AuthError("Sign in required", 401)
        key = "session:" + token
        self._check_throttle(key)
        if not verify_password(current, self.password_hash):
            self._record_failure(key)
            raise AuthError("Current password is wrong", 403)
        self._clear_failures(key)
        _check_password(new)
        self.password_hash = hash_password(new, iterations=self.iterations)
        self._save(self.path)
        with self._lock:
            self._sessions = {t: s for t, s in self._sessions.items() if t == token}

    def _check_throttle(self, client: str) -> None:
        with self._lock:
            locked_until = self._failures.get(client, (0, 0.0))[1]
        wait = locked_until - self.clock()
        if wait > 0:
            raise Throttled(math.ceil(wait))

    def _record_failure(self, client: str) -> None:
        """Counts a failed attempt; the MAX_FAILURES-th locks the client out for LOCKOUT_SECONDS."""
        with self._lock:
            count = self._failures.get(client, (0, 0.0))[0] + 1
            if count >= MAX_FAILURES:
                self._failures[client] = (0, self.clock() + LOCKOUT_SECONDS)
            else:
                self._failures[client] = (count, 0.0)

    def _clear_failures(self, client: str) -> None:
        with self._lock:
            self._failures.pop(client, None)

    def _save(self, path: str) -> None:
        """Writes the account to ``path`` atomically, readable only by its owner."""
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"username": self.username, "password": self.password_hash}, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)

    def _new_session(self) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[token] = (self.username, self.clock() + SESSION_SECONDS)
        return token


def _check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Use a password of at least {MIN_PASSWORD_LENGTH} characters")


def _load_account(path: str) -> tuple[str, str]:
    """Reads ``(username, password_hash)`` from an auth file.

    Raises:
        FileNotFoundError: There is no file (and no dangling symlink) at ``path``: first run.
        AuthError: Anything else that stops the account being read.
    """
    try:
        with open(path) as f:
            data = json.load(f)
        username, password_hash = data["username"], data["password"]
    except FileNotFoundError:
        if not os.path.islink(path):
            raise
        username = password_hash = None
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
        # env: references are looked up in the same environment as everything else.
        password = (
            environ.get(raw_password[4:], "") if raw_password.startswith("env:") else resolve_secret(raw_password)
        )
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
    try:
        username, password_hash = _load_account(path)
        return Auth("file", username=username, password_hash=password_hash, path=path, iterations=iterations)
    except FileNotFoundError:
        pass
    code = secrets.token_hex(4)
    logger.warning("No admin account yet. Open pi-hub and enter setup code %s to create one.", code)
    return Auth("file", path=path, setup_code=code, iterations=iterations)
