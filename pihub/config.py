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

from .integrations import KINDS

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


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "service"


def resolve_secret(value):
    """Return the secret a config value points at ('' if unset or unresolvable)."""
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


def is_reference(value):
    return isinstance(value, str) and value.startswith(("env:", "file:"))


def _normalise_service(raw, categories):
    """Validate one service entry and fill defaults. Raises ConfigError."""
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
    svc = {
        "id": slugify(str(raw.get("id") or name)),
        "name": name[:40],
        "kind": kind,
        "category": raw.get("category") if raw.get("category") in categories else categories[-1],
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
    for field in ("username", "log"):
        if raw.get(field):
            svc[field] = str(raw[field])
    for field in SECRET_FIELDS:
        if raw.get(field):
            svc[field] = str(raw[field])
    return svc


def _clean_path(value, name, field):
    value = str(value or "").strip()
    if value and not value.startswith("/"):
        value = "/" + value
    value = value.rstrip("/") if value != "/" else ""
    if not _PATH_RE.match(value):
        raise ConfigError(f"{name}: {field} must be a URL path like /web")
    return value


def normalise(raw):
    """Validate a whole config document and return a normalised copy."""
    if not isinstance(raw, dict):
        raise ConfigError("Config must be a JSON object")
    categories = raw.get("categories") or DEFAULT_CATEGORIES
    if not isinstance(categories, list) or not all(isinstance(c, str) and c for c in categories):
        raise ConfigError("categories must be a list of names")
    services, seen = [], set()
    for entry in raw.get("services", []):
        svc = _normalise_service(entry, categories)
        base, n = svc["id"], 2
        while svc["id"] in seen:
            svc["id"], n = f"{base}-{n}", n + 1
        seen.add(svc["id"])
        services.append(svc)
    schedule = []
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


def _poll_seconds(value):
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"pollSeconds must be a whole number of seconds, not {value!r}") from None
    return max(5, seconds)


def load(path):
    try:
        with open(path) as f:
            raw = json.load(f)
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {path} (copy config.example.json to get started)") from None
    except ValueError as e:
        raise ConfigError(f"{path} is not valid JSON: {e}") from None
    return normalise(raw)


def save(path, cfg):
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def public_services(cfg):
    """Service list for the settings UI: literal secrets replaced by flags."""
    out = []
    for svc in cfg["services"]:
        view = {k: v for k, v in svc.items() if k not in SECRET_FIELDS}
        for field in SECRET_FIELDS:
            value = svc.get(field)
            view[f"{field}Set"] = bool(value)
            view[f"{field}Ref"] = value if is_reference(value) else ""
        out.append(view)
    return out


def apply_update(cfg, payload):
    """Merge services edited in the UI into cfg. Returns (new_cfg, notices).

    Secret fields are write-only: an empty value keeps the stored secret,
    ``clearApiKey``/``clearPassword`` removes it, and any change to where the
    service lives (TARGET_FIELDS) drops stored secrets so they can't be
    redirected to another host.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("services"), list):
        raise ConfigError('Expected {"services": [...]}')
    old = {s["id"]: s for s in cfg["services"]}
    merged, notices = [], []
    for entry in payload["services"]:
        if not isinstance(entry, dict) or not str(entry.get("name", "")).strip():
            continue  # blank rows are ignored (removed)
        prev = old.get(entry.get("id"), {})
        svc = {**prev, **{k: v for k, v in entry.items() if k not in SECRET_FIELDS and not k.endswith(("Set", "Ref"))}}
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
    new_cfg = copy.deepcopy(cfg)
    new_cfg["services"] = merged
    return normalise(new_cfg), notices
