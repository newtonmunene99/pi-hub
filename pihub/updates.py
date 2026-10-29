"""Container image updates reported by Diun.

Diun (https://crazymax.dev/diun/) watches the images of running containers and
calls a webhook when a newer one is pushed. pi-hub keeps one entry per image
until the admin dismisses it, saved to ``updates.json`` next to the config.

Only reports with ``status: "update"`` are kept: Diun sends ``"new"`` the first
time it sees an image, which says nothing about an update.
"""

import json
import os
import threading
import time
from collections.abc import Callable
from typing import Any

MAX_ENTRIES = 200


class UpdatesError(ValueError):
    """Bad webhook payload or token setting; the message is safe to show."""


class Updates:
    """Thread-safe store of image updates, keyed by image.

    Each entry is ``{"image", "digest", "hubLink", "created", "containers",
    "received"}``: ``containers`` are the sorted names of the containers
    running the image, and ``received`` is when this digest was first reported
    (epoch seconds).
    """

    def __init__(self, path: str | None = None, clock: Callable[[], float] = time.time) -> None:
        """Loads entries from ``path``; a missing or corrupt file starts empty.

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
                if isinstance(loaded, dict) and all(isinstance(e, dict) and "received" in e for e in loaded.values()):
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
