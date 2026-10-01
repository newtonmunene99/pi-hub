"""Small formatting helpers shared by the collectors and the API."""

from datetime import datetime


def fmt_duration(seconds: float) -> str:
    """Compact duration: '41d 6h', '3h 12m', '7m'."""
    seconds = int(max(seconds, 0))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def fmt_speed(bytes_per_s: float) -> str:
    """Megabytes per second with one decimal, e.g. "18.4 MB/s"."""
    mb = bytes_per_s / 1_000_000
    return f"{mb:.1f} MB/s" if mb >= 0.1 else "0 MB/s"


def plural(n: int, word: str) -> str:
    """'1 stream', '2,000 titles' (thousands separated)."""
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def fmt_size(num_bytes: float) -> str:
    """Human size with one unit for the whole value: '931 GB', '3.6 TB'."""
    if num_bytes >= 2e12:
        return f"{num_bytes / 1e12:.1f} TB"
    return f"{num_bytes / 1e9:.0f} GB"


def day_label(when: datetime, now: datetime | None = None) -> str:
    """'today', 'yesterday' or a short weekday for a datetime."""
    now = now or datetime.now()
    delta = (now.date() - when.date()).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "yesterday"
    return when.strftime("%a")
