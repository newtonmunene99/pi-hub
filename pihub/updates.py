"""Container image updates reported by Diun.

Diun (https://crazymax.dev/diun/) watches the images of running containers and
calls a webhook when a newer one is pushed. pi-hub keeps one entry per image
until the admin dismisses it, saved to ``updates.json`` next to the config.

Only reports with ``status: "update"`` are kept: Diun sends ``"new"`` the first
time it sees an image, which says nothing about an update.
"""

import json
import os
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .config import resolve_secret

MAX_ENTRIES = 200
MIN_TOKEN_LENGTH = 32


class UpdatesError(ValueError):
    """Bad webhook payload or token setting; the message is safe to show."""


def token_from_env(environ: Mapping[str, str]) -> str:
    """The webhook token from ``PIHUB_WEBHOOK_TOKEN`` (``env:``/``file:`` references allowed).

    Returns:
        The token, or ``""`` when the variable is unset (the webhook is off).

    Raises:
        UpdatesError: It is set but resolves to fewer than 32 characters. A
            long random token is what keeps the unthrottled hook unguessable.
    """
    raw = environ.get("PIHUB_WEBHOOK_TOKEN", "")
    if not raw:
        return ""
    token = resolve_secret(raw)
    if len(token) < MIN_TOKEN_LENGTH:
        raise UpdatesError(f"PIHUB_WEBHOOK_TOKEN must be at least {MIN_TOKEN_LENGTH} characters")
    return token


def updates_path(environ: Mapping[str, str], config_path: str) -> str:
    """Where updates are saved: ``PIHUB_UPDATES``, else ``updates.json`` next to the config."""
    return environ.get("PIHUB_UPDATES") or os.path.join(os.path.dirname(config_path), "updates.json")


def _valid_entry(entry: object) -> bool:
    """True if a loaded entry has the shape ``record`` writes, so reading it cannot fail later."""
    return (
        isinstance(entry, dict)
        and all(isinstance(entry.get(k), str) for k in ("image", "digest", "hubLink", "created"))
        and isinstance(entry.get("containers"), list)
        and all(isinstance(c, str) for c in entry["containers"])
        and isinstance(entry.get("received"), int | float)
        and not isinstance(entry["received"], bool)
    )


def normalise_name(text: str) -> str:
    """Lowercase alphanumerics only, so "Media_Server" and "media-server" compare equal."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def split_image(image: str) -> tuple[str, str]:
    """Splits ``"ghcr.io/x/y:0.4"`` into ``("ghcr.io/x/y", "0.4")``; no tag means ``latest``.

    A colon before the last ``/`` is a registry port, not a tag.
    """
    name = image.split("@", 1)[0]
    path, sep, tag = name.rpartition(":")
    if not sep or "/" in tag:
        return name, "latest"
    return path, tag


class Updates:
    """Thread-safe store of image updates, keyed by image.

    Each entry is ``{"image", "digest", "hubLink", "created", "containers",
    "received"}``: ``containers`` are the sorted names of the containers
    running the image, and ``received`` is when this digest was first reported
    (epoch seconds).
    """

    def __init__(self, path: str | None = None, clock: Callable[[], float] = time.time) -> None:
        """Loads entries from ``path``; a missing or corrupt file, or any malformed entry, starts empty.

        Args:
            path: Where entries are saved. None keeps them in memory only.
            clock: Returns the current time; replaced in tests.
        """
        self.path = path
        self.clock = clock
        self.lock = threading.Lock()
        self.data: dict[str, dict[str, Any]] = {}
        if path:
            try:
                with open(path) as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict) and all(_valid_entry(e) for e in loaded.values()):
                    self.data = loaded
            except (OSError, ValueError):
                self.data = {}

    def record(self, payload: object) -> bool:
        """Stores one Diun webhook body.

        Returns:
            True if it was an update and is now stored, False if it was ignored.

        Raises:
            UpdatesError: The body is not an object with string ``status``,
                ``image`` and ``digest``.
        """
        if not isinstance(payload, dict) or not all(
            isinstance(payload.get(k), str) for k in ("status", "image", "digest")
        ):
            raise UpdatesError("Expected a Diun webhook body with status, image and digest")
        if payload["status"] != "update":
            return False
        meta = payload.get("metadata")
        names = str(meta.get("ctn_names", "")) if isinstance(meta, dict) else ""
        containers = {n.strip() for n in names.split(",") if n.strip()}
        link = str(payload.get("hub_link") or "")
        image = payload["image"]
        with self.lock:
            old = self.data.get(image)
            if old and old["digest"] == payload["digest"]:
                if containers <= set(old["containers"]):
                    return True
                old["containers"] = sorted(containers | set(old["containers"]))
            else:
                self.data[image] = {
                    "image": image,
                    "digest": payload["digest"],
                    "hubLink": link if link.startswith(("http://", "https://")) else "",
                    "created": str(payload.get("created") or ""),
                    "containers": sorted(containers),
                    "received": self.clock(),
                }
                while len(self.data) > MAX_ENTRIES:
                    del self.data[min(self.data, key=lambda k: self.data[k]["received"])]
            self._save()
        return True

    def entries(self) -> list[dict[str, Any]]:
        """Copies of the stored entries, newest ``received`` first."""
        with self.lock:
            items = [{**e, "containers": list(e["containers"])} for e in self.data.values()]
        return sorted(items, key=lambda e: e["received"], reverse=True)

    def dismiss(self, image: str | None = None, *, all: bool = False) -> None:
        """Forgets one image's update, or every update with ``all``; an unknown image is ignored."""
        with self.lock:
            if all:
                self.data.clear()
            elif self.data.pop(image or "", None) is None:
                return
            self._save()

    def view(self, services: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """The ``updates`` list for /api/state, matched to the dashboard's services.

        An entry matches a service when one of its container names equals the
        service's ``container`` field, or its id or name when that field is
        empty (compared with ``normalise_name``).

        Args:
            services: Service views with ``id``, ``name`` and ``container``.

        Returns:
            Entries newest first, each with ``tag``, ``services`` (matched ids)
            and ``label`` (the first matched service's name, else the first
            container name, else the image without its tag) added.
        """
        out = []
        for entry in self.entries():
            names = {normalise_name(c) for c in entry["containers"]}
            matched = [
                s
                for s in services
                if names
                & (
                    {normalise_name(s["container"])}
                    if s.get("container")
                    else {normalise_name(s["id"]), normalise_name(s["name"])}
                )
            ]
            path, tag = split_image(entry["image"])
            label = matched[0]["name"] if matched else (entry["containers"] or [path])[0]
            out.append({**entry, "tag": tag, "services": [s["id"] for s in matched], "label": label})
        return out

    def _save(self) -> None:
        """Writes entries atomically, readable only by the owner. Call with the lock held."""
        if not self.path:
            return
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self.data, f, indent=2)
            f.write("\n")
        os.replace(tmp, self.path)
