"""Scheduled jobs shown in the sidebar ("Kometa · 03:00 · in 5h").

Pi Hub doesn't run these jobs; it shows when they next run and, optionally,
when they last produced output (newest file matching ``lastRun.glob`` in
``lastRun.dir``).
"""

import glob
import os
from datetime import datetime, timedelta

from .util import day_label


def next_run(times, now=None):
    now = now or datetime.now()
    candidates = []
    for t in times:
        hour, minute = map(int, t.split(":"))
        at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        candidates.append(at if at > now else at + timedelta(days=1))
    return min(candidates)


def until(when, now=None):
    seconds = (when - (now or datetime.now())).total_seconds()
    if seconds >= 3600:
        return f"in {int(seconds // 3600)}h"
    return f"in {max(int(seconds // 60), 1)}m"


def last_output(spec, now=None):
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


def entries(schedule, now=None):
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
