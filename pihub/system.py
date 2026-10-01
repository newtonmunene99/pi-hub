"""Host statistics read from /proc and /sys (works inside a container too).

Each stat is optional: anything the host doesn't expose is simply left out.
"""

import glob
import os
import re
import shutil

from .util import fmt_duration, fmt_size

PROC = os.environ.get("PIHUB_PROC", "/proc")
SYS = os.environ.get("PIHUB_SYS", "/sys")


def _read(path):
    with open(path) as f:
        return f.read()


class CpuMeter:
    """CPU busy % between consecutive calls, from /proc/stat."""

    def __init__(self):
        self.prev = None

    def percent(self):
        vals = list(map(int, _read(f"{PROC}/stat").splitlines()[0].split()[1:]))
        idle, total = vals[3] + vals[4], sum(vals)
        pct = 0.0
        if self.prev:
            d_total, d_idle = total - self.prev[1], idle - self.prev[0]
            pct = 100.0 * (1 - d_idle / d_total) if d_total else 0.0
        self.prev = (idle, total)
        return max(0.0, min(pct, 100.0))


def cpu_temperature():
    """CPU temperature in °C, preferring a zone whose type mentions cpu/soc/pkg."""
    zones = sorted(glob.glob(f"{SYS}/class/thermal/thermal_zone*"))
    ranked = []
    for zone in zones:
        try:
            kind = _read(f"{zone}/type").strip().lower()
            temp = int(_read(f"{zone}/temp")) / 1000
        except (OSError, ValueError):
            continue
        ranked.append((0 if re.search(r"cpu|soc|pkg|k10temp", kind) else 1, temp))
    return sorted(ranked)[0][1] if ranked else None


def argon_fan_speed(temp, conf_text):
    """Fan % the Argon ONE daemon applies at ``temp`` for an argononed.conf.

    Mirrors argononed's step logic: the highest threshold <= temp wins and
    any non-zero speed is raised to at least 25%.
    """
    steps = []
    for line in conf_text.splitlines():
        m = re.match(r"\s*([\d.]+)\s*=\s*([\d.]+)", line)
        if m:
            steps.append((float(m.group(1)), int(float(m.group(2)))))
    for threshold, speed in sorted(steps, reverse=True):
        if temp >= threshold:
            return 0 if speed < 1 else max(speed, 25)
    return 0


def fan_text(fan_cfg, temp):
    """Short fan description for the CPU card, or '' if not configured."""
    kind = (fan_cfg or {}).get("type")
    try:
        if kind == "argon" and temp is not None:
            speed = argon_fan_speed(temp, _read(fan_cfg.get("config", "/argononed.conf")))
            return "fan off" if speed == 0 else f"fan {speed}%"
        if kind == "hwmon":
            for path in sorted(glob.glob(f"{SYS}/class/hwmon/hwmon*/fan*_input")):
                rpm = int(_read(path))
                return "fan off" if rpm == 0 else f"fan {rpm} rpm"
    except (OSError, ValueError):
        pass
    return ""


def board_name(override=""):
    if override:
        return override
    try:
        model = _read(f"{PROC}/device-tree/model").strip("\x00\n ")
    except OSError:
        return ""
    m = re.match(r"Raspberry Pi (\d+|Zero \w*|Compute Module \d+)", model)
    return f"Pi {m.group(1)}" if m else model.split(" Rev ")[0]


def memory():
    info = {}
    for line in _read(f"{PROC}/meminfo").splitlines():
        key, _, rest = line.partition(":")
        info[key] = int(rest.split()[0]) * 1024
    total = info["MemTotal"]
    return total - info.get("MemAvailable", info.get("MemFree", 0)), total


def disk_cards(disks):
    cards = []
    for disk in disks:
        try:
            du = shutil.disk_usage(disk["path"])
        except OSError:
            cards.append({"label": disk["name"], "value": "—", "sub": "not mounted", "pct": 0, "warn": True})
            continue
        pct = du.used / du.total * 100 if du.total else 0
        sub = f"of {fmt_size(du.total)} · {pct:.0f}%"
        if pct >= 90:
            sub += f" · {du.free / 1e9:.1f} GB free"
        used = fmt_size(du.used).split()[0]
        cards.append({"label": disk["name"], "value": used, "sub": sub, "pct": pct, "warn": pct >= 90})
    return cards


def collect(system_cfg, cpu_meter):
    """All system cards for the stats row plus host uptime."""
    cards = []
    temp = cpu_temperature() if system_cfg.get("temperature", True) else None
    if temp is not None:
        detail = " · ".join(
            x for x in (board_name(system_cfg.get("label", "")), fan_text(system_cfg.get("fan"), temp)) if x
        )
        cards.append(
            {
                "label": "CPU temp",
                "value": f"{temp:.0f}°",
                "sub": detail,
                "pct": min(temp / 85 * 100, 100),
                "warn": temp >= 75,
            }
        )
    try:
        cpu = cpu_meter.percent()
        load1 = float(_read(f"{PROC}/loadavg").split()[0])
        cards.append(
            {
                "label": "CPU load",
                "value": f"{cpu:.0f}%",
                "sub": f"{os.cpu_count()} cores · {load1:.2f} load",
                "pct": cpu,
                "warn": cpu >= 90,
            }
        )
    except (OSError, ValueError, IndexError):
        pass
    try:
        used, total = memory()
        cards.append(
            {
                "label": "Memory",
                "value": f"{used / 1e9:.1f}",
                "sub": f"of {total / 1e9:.0f} GB",
                "pct": used / total * 100,
                "warn": used / total >= 0.9,
            }
        )
    except (OSError, KeyError, ValueError):
        pass
    cards.extend(disk_cards(system_cfg.get("disks", [])))
    try:
        uptime = fmt_duration(float(_read(f"{PROC}/uptime").split()[0]))
    except (OSError, ValueError):
        uptime = ""
    return {"cards": cards, "uptime": uptime}
