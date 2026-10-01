import os
import tempfile
import unittest
from datetime import datetime

from pihub import schedule, system
from pihub.hub import History
from pihub.util import day_label, fmt_duration, fmt_size, fmt_speed, plural

ARGON = """#
# Argon Fan Speed Configuration (CPU)
#
55=30
60=50
65=70
70=100
"""


class ArgonTests(unittest.TestCase):
    def test_steps(self):
        cases = {40: 0, 54.9: 0, 55: 30, 59: 30, 62: 50, 69: 70, 85: 100}
        for temp, expected in cases.items():
            with self.subTest(temp=temp):
                self.assertEqual(system.argon_fan_speed(temp, ARGON), expected)

    def test_minimum_speed_is_25(self):
        self.assertEqual(system.argon_fan_speed(50, "50=10"), 25)


class DiskTests(unittest.TestCase):
    def test_existing_and_missing_disks(self):
        with tempfile.TemporaryDirectory() as d:
            cards = system.disk_cards([{"name": "media", "path": d}, {"name": "gone", "path": "/nonexistent/x"}])
        self.assertEqual(cards[0]["label"], "media")
        self.assertIn("of ", cards[0]["sub"])
        self.assertEqual(cards[1]["sub"], "not mounted")
        self.assertTrue(cards[1]["warn"])


class CollectTests(unittest.TestCase):
    def test_collect_on_this_host(self):
        result = system.collect({"disks": []}, system.CpuMeter())
        labels = [c["label"] for c in result["cards"]]
        if os.path.exists("/proc/meminfo"):
            self.assertIn("Memory", labels)
            self.assertTrue(result["uptime"])


class ScheduleTests(unittest.TestCase):
    now = datetime(2026, 9, 30, 22, 0)

    def test_next_run_picks_soonest(self):
        self.assertEqual(schedule.next_run(["03:00", "23:00"], self.now), datetime(2026, 9, 30, 23, 0))
        self.assertEqual(schedule.next_run(["03:00"], self.now), datetime(2026, 10, 1, 3, 0))

    def test_entries_sorted_by_next_run(self):
        jobs = [{"name": "Late", "at": ["05:00"]}, {"name": "Early", "at": ["03:00"]}]
        self.assertEqual([e["name"] for e in schedule.entries(jobs, self.now)], ["Early", "Late"])
        self.assertEqual(schedule.entries(jobs, self.now)[0]["when"], "03:00 · in 5h")

    def test_last_output(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "backup.tar.zst")
            with open(path, "wb") as f:
                f.write(b"x" * 2_000_000)
            note = schedule.last_output({"dir": d, "glob": "*.tar.zst"})
        self.assertRegex(note, r"^Last today \d\d:\d\d · 2 MB$")
        self.assertIsNone(schedule.last_output({"dir": "/nonexistent"}))


class HistoryTests(unittest.TestCase):
    def test_bars_and_since(self):
        h = History()
        t = 1_000_000 * 3600.0
        h.record("a", True, 10, now=t)
        h.record("a", True, 30, now=t + 60)
        h.record("a", False, None, now=t + 3600)
        bars = h.bars("a", now=t + 3600)
        self.assertEqual(len(bars), 24)
        self.assertEqual(bars[-2], {"ms": 20.0, "state": "up"})
        self.assertEqual(bars[-1], {"ms": None, "state": "down"})
        self.assertEqual(h.since("a", now=t + 3600 + 120), "2m")

    def test_old_buckets_pruned(self):
        h = History()
        h.record("a", True, 10, now=0)
        h.record("a", True, 10, now=30 * 3600)
        self.assertEqual(len(h.data["a"]["hours"]), 1)


class UtilTests(unittest.TestCase):
    def test_formatting(self):
        self.assertEqual(fmt_duration(41 * 86400 + 6 * 3600), "41d 6h")
        self.assertEqual(fmt_duration(3 * 3600 + 12 * 60), "3h 12m")
        self.assertEqual(fmt_speed(18_400_000), "18.4 MB/s")
        self.assertEqual(fmt_speed(10), "0 MB/s")
        self.assertEqual(plural(1, "stream"), "1 stream")
        self.assertEqual(plural(2000, "title"), "2,000 titles")
        self.assertEqual(fmt_size(931e9), "931 GB")
        self.assertEqual(fmt_size(3.6e12), "3.6 TB")
        now = datetime(2026, 9, 30, 12)
        self.assertEqual(day_label(datetime(2026, 9, 30, 3), now), "today")
        self.assertEqual(day_label(datetime(2026, 9, 29, 3), now), "yesterday")


if __name__ == "__main__":
    unittest.main()
