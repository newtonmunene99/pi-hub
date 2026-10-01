"""Loading, validating and updating config.json.

The file holds everything Pi Hub needs: the services to show, which disks and
sensors to read, and scheduled jobs. See docs/configuration.md.

Secrets (``apiKey``, ``password``) may be written literally or as references:
``"env:RADARR_API_KEY"`` reads an environment variable and
``"file:/run/secrets/radarr"`` reads a file. Literal secrets are never returned
by the API; references are shown by name only.
"""

import copy
import json
import os
import re
from collections.abc import Mapping, Sequence
from typing import Any, cast

from .integrations import KINDS
from .models import Config, Service

DEFAULT_CATEGORIES = ["Media", "Automation", "Downloads", "System"]
SECRET_FIELDS = ("apiKey", "password")
# Changing any of these changes where secrets are sent, so stored secrets are
# dropped unless new ones are supplied in the same update.
TARGET_FIELDS = ("kind", "scheme", "host", "port", "basePath")

_HOST_RE = re.compile(r"^[A-Za-z0-9.\-]+$|^\[[0-9A-Fa-f:.]+\]$")
_PATH_RE = re.compile(r"^(/[^\s?#]*)?$")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ConfigError(ValueError):
    """Raised for invalid configuration; the message is safe to show users."""


def slugify(text: str) -> str:
    """Lowercase, hyphen-separated id from a name: "Radarr 4K" -> "radarr-4k"."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "service"


def resolve_secret(value: str | None) -> str:
    """Returns the secret a config value points at.

    Args:
        value: A literal secret, ``"env:NAME"`` or ``"file:/path"``.

    Returns:
        The secret, or ``""`` if the value is empty or the variable or file is
        missing. Never raises, so a missing secret degrades to "no stats".
    """
    if not value:
        return ""
    value = str(value)
    if value.startswith("env:"):
        return os.environ.get(value[4:], "")
    if value.startswith("file:"):
        try:
            with open(value[5:]) as f:
                return f.read().strip()
        except OSError:
            return ""
    return value


def is_reference(value: object) -> bool:
    """True for ``env:``/``file:`` references, which are safe to show in the UI."""
    return isinstance(value, str) and value.startswith(("env:", "file:"))


def _normalise_service(raw: object, categories: Sequence[str]) -> Service:
    """Validates one service entry and fills in defaults.

    Raises:
        ConfigError: The entry is not an object or a field is invalid.
    """
    if not isinstance(raw, dict):
        raise ConfigError("Each service must be an object")
    name = str(raw.get("name", "")).strip()
    if not name:
        raise ConfigError("Every service needs a name")
    kind = raw.get("kind", "generic")
    if kind not in KINDS:
        raise ConfigError(f"{name}: unknown kind '{kind}' (choose from {', '.join(sorted(KINDS))})")
    port = str(raw.get("port", "") or "").strip()
    if port and (not port.isdigit() or not 0 < int(port) < 65536):
        raise ConfigError(f"{name}: port must be a number between 1 and 65535")
    if KINDS[kind].needs_port and not port:
        raise ConfigError(f"{name}: a port is required for {KINDS[kind].label}")
    host = str(raw.get("host", "") or "127.0.0.1").strip()
    if not _HOST_RE.match(host):
        raise ConfigError(f"{name}: host '{host}' is not a valid hostname or IP")
    scheme = raw.get("scheme", "http")
    if scheme not in ("http", "https"):
        raise ConfigError(f"{name}: scheme must be http or https")
    svc: Service = {
        "id": slugify(str(raw.get("id") or name)),
        "name": name[:40],
        "kind": kind,
        "category": str(raw["category"]) if raw.get("category") in categories else categories[-1],
        "scheme": scheme,
        "host": host,
        "port": port,
        "basePath": _clean_path(raw.get("basePath", ""), name, "basePath"),
        "linkPath": _clean_path(raw.get("linkPath", ""), name, "linkPath"),
        "url": str(raw.get("url", "") or "").strip(),
        "description": str(raw.get("description", "") or "")[:60],
        "icon": str(raw.get("icon", "") or name[:1])[:2],
        "pinned": bool(raw.get("pinned", False)),
    }
    if svc["url"] and not svc["url"].startswith(("http://", "https://")):
        raise ConfigError(f"{name}: url must start with http:// or https://")
    # Optional keys are only stored when set, so an unset secret never
    # appears in config.json as an empty string.
    for field in ("username", "log", *SECRET_FIELDS):
        if raw.get(field):
            cast(dict[str, Any], svc)[field] = str(raw[field])
    return svc


def _clean_path(value: object, name: str, field: str) -> str:
    """Normalises a URL path to "" or "/x/y" (leading slash, no trailing slash)."""
    value = str(value or "").strip()
    if value and not value.startswith("/"):
        value = "/" + value
    value = value.rstrip("/") if value != "/" else ""
    if not _PATH_RE.match(value):
        raise ConfigError(f"{name}: {field} must be a URL path like /web")
    return value


def normalise(raw: object) -> Config:
    """Validates a whole config document and returns a normalised copy.

    Fills every default, turns duplicate service ids into ``id-2``, ``id-3``
    and so on, and accepts a single ``"at"`` time as well as a list.

    Args:
        raw: The parsed config.json, or a config being re-validated.

    Returns:
        A new, fully populated config; ``raw`` is not modified.

    Raises:
        ConfigError: Anything is invalid. The message names the problem in
            terms a user can act on.
    """
    if not isinstance(raw, dict):
        raise ConfigError("Config must be a JSON object")
    categories = raw.get("categories") or DEFAULT_CATEGORIES
    if not isinstance(categories, list) or not all(isinstance(c, str) and c for c in categories):
        raise ConfigError("categories must be a list of names")
    services: list[Service] = []
    seen: set[str] = set()
    for entry in raw.get("services", []):
        svc = _normalise_service(entry, categories)
        base, n = svc["id"], 2
        while svc["id"] in seen:
            svc["id"], n = f"{base}-{n}", n + 1
        seen.add(svc["id"])
        services.append(svc)
    schedule: list[dict[str, Any]] = []
    for job in raw.get("schedule", []):
        times = job.get("at", [])
        times = [times] if isinstance(times, str) else list(times)
        if not job.get("name") or not times or not all(_TIME_RE.match(t) for t in times):
            raise ConfigError("Each schedule entry needs a name and 'at' times like \"03:00\"")
        schedule.append({**job, "at": times})
    system = raw.get("system", {}) or {}
    for disk in system.get("disks", []):
        if not disk.get("name") or not disk.get("path"):
            raise ConfigError("Each disk needs a name and a path")
    return {
        "title": str(raw.get("title", "pi-hub"))[:30],
        "pollSeconds": _poll_seconds(raw.get("pollSeconds", 15)),
        "categories": categories,
        "system": system,
        "schedule": schedule,
        "services": services,
    }


def _poll_seconds(value: object) -> int:
    try:
        seconds = int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        raise ConfigError(f"pollSeconds must be a whole number of seconds, not {value!r}") from None
    return max(5, seconds)


def load(path: str) -> Config:
    """Reads and validates a config file.

    Raises:
        ConfigError: The file is missing, is not JSON, or fails validation.
    """
    try:
        with open(path) as f:
            raw = json.load(f)
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {path} (copy config.example.json to get started)") from None
    except ValueError as e:
        raise ConfigError(f"{path} is not valid JSON: {e}") from None
    return normalise(raw)


def save(path: str, cfg: Config) -> None:
    """Writes the config atomically, readable only by its owner (it may hold secrets).

    The file is written to ``path + ".tmp"`` and renamed over the old one, so
    a crash mid-write never leaves a truncated config.
    """
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def public_services(cfg: Config) -> list[dict[str, Any]]:
    """Service list for the settings UI, safe to send to a browser.

    Each secret field is replaced by ``<field>Set`` (whether a value is stored)
    and ``<field>Ref`` (the reference name if it is ``env:``/``file:``, else
    ``""``). Literal secrets are never included.
    """
    out = []
    for svc in cfg["services"]:
        view: dict[str, Any] = {k: v for k, v in svc.items() if k not in SECRET_FIELDS}
        for field in SECRET_FIELDS:
            value = svc.get(field)
            view[f"{field}Set"] = bool(value)
            view[f"{field}Ref"] = value if is_reference(value) else ""
        out.append(view)
    return out


def apply_update(cfg: Config, payload: object) -> tuple[Config, list[str]]:
    """Merges services edited in the settings page into a config.

    Secret fields are write-only: an empty value keeps the stored secret,
    ``clearApiKey``/``clearPassword`` removes it, and any change to where the
    service lives (``TARGET_FIELDS``) drops stored secrets so they cannot be
    redirected to another host. Fields the UI does not send (such as ``log``)
    are kept from the stored service.

    Args:
        cfg: The current config; not modified.
        payload: The request body, ``{"services": [...]}``. Rows with a blank
            name are treated as removed.

    Returns:
        The new, validated config and a list of notices for the user (for
        example that a key was dropped because the address changed).

    Raises:
        ConfigError: The payload is malformed or the result is invalid.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("services"), list):
        raise ConfigError('Expected {"services": [...]}')
    old: dict[str, Mapping[str, Any]] = {s["id"]: s for s in cfg["services"]}
    merged: list[dict[str, Any]] = []
    notices: list[str] = []
    for entry in payload["services"]:
        # A blank row is a service the user removed.
        if not isinstance(entry, dict) or not str(entry.get("name", "")).strip():
            continue
        prev = old.get(entry.get("id", ""), {})
        svc: dict[str, Any] = {
            **prev,
            **{k: v for k, v in entry.items() if k not in SECRET_FIELDS and not k.endswith(("Set", "Ref"))},
        }
        if not prev:
            svc.pop("id", None)
        moved = prev and any(str(svc.get(f, "")) != str(prev.get(f, "")) for f in TARGET_FIELDS)
        for field in SECRET_FIELDS:
            new_value = str(entry.get(field) or "").strip()
            clear = entry.get("clear" + field[0].upper() + field[1:])
            if new_value:
                svc[field] = new_value
            elif clear or (moved and prev.get(field)):
                svc.pop(field, None)
                if moved and not clear and prev.get(field):
                    notices.append(f"{svc['name']}: address changed, so its {field} was removed - enter it again.")
        merged.append(svc)
    new_cfg: dict[str, Any] = {**copy.deepcopy(cfg), "services": merged}
    return normalise(new_cfg), notices
