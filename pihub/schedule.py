"""Scheduled jobs shown in the sidebar ("Kometa · 03:00 · in 5h").

Pi Hub doesn't run these jobs; it shows when they next run and, optionally,
when they last produced output (newest file matching ``lastRun.glob`` in
``lastRun.dir``).
"""

import glob
import os
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from .util import day_label


def next_run(times: Sequence[str], now: datetime | None = None) -> datetime:
    """The soonest upcoming occurrence of any of the "HH:MM" times."""
    now = now or datetime.now()
    candidates = []
    for t in times:
        hour, minute = map(int, t.split(":"))
        at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        candidates.append(at if at > now else at + timedelta(days=1))
    return min(candidates)


def until(when: datetime, now: datetime | None = None) -> str:
    """Countdown such as "in 5h" or "in 12m" (never "in 0m")."""
    seconds = (when - (now or datetime.now())).total_seconds()
    if seconds >= 3600:
        return f"in {int(seconds // 3600)}h"
    return f"in {max(int(seconds // 60), 1)}m"


def last_output(spec: Mapping[str, str] | None, now: datetime | None = None) -> str | None:
    """'Last today 05:00 · 85 MB' for the newest matching file, or None."""
    if not spec or not spec.get("dir"):
        return None
    files = glob.glob(os.path.join(spec["dir"], spec.get("glob", "*")))
    files = [f for f in files if os.path.isfile(f)]
    if not files:
        return None
    newest = max(files, key=os.path.getmtime)
    st = os.stat(newest)
    when = datetime.fromtimestamp(st.st_mtime)
    return f"Last {day_label(when, now)} {when:%H:%M} · {st.st_size / 1e6:.0f} MB"


def entries(schedule: Iterable[Mapping[str, Any]], now: datetime | None = None) -> list[dict[str, Any]]:
    """Sidebar rows for scheduled jobs, soonest first."""
    upcoming = sorted(((next_run(job["at"], now), job) for job in schedule), key=lambda pair: pair[0])
    return [
        {
            "name": job["name"],
            "service": job.get("service", ""),
            "when": f"{at:%H:%M} · {until(at, now)}",
            "note": last_output(job.get("lastRun"), now),
        }
        for at, job in upcoming
    ]
