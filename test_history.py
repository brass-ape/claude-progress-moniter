from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from database import connect_database
from history import UsageHistory
from usage import UsageSnapshot


def _make_snapshot(
    five_pct: int = 10,
    week_pct: int = 5,
    fetched_at: datetime | None = None,
) -> UsageSnapshot:
    now = fetched_at or datetime.now(timezone.utc)
    return UsageSnapshot(
        five_hour_percent=five_pct,
        weekly_percent=week_pct,
        five_hour_reset=None,
        weekly_reset=None,
        five_hour_remaining="--",
        weekly_remaining="--",
        five_hour_reset_label="--:--",
        weekly_reset_label="--:--",
        clock_time="12:00:00",
        clock_date="Mon 6 Jul",
        fetched_at=now,
        api_latency_ms=42,
    )


def _in_memory_history() -> UsageHistory:
    conn = connect_database(":memory:")
    return UsageHistory(conn)


class LatestRowTests(unittest.TestCase):
    def test_returns_none_on_empty_db(self) -> None:
        h = _in_memory_history()
        self.assertIsNone(h.latest_row())

    def test_returns_most_recent_row(self) -> None:
        h = _in_memory_history()
        now = datetime.now(timezone.utc)
        h.record(_make_snapshot(five_pct=10, fetched_at=now - timedelta(minutes=10)))
        h.record(_make_snapshot(five_pct=20, fetched_at=now))
        row = h.latest_row()
        self.assertIsNotNone(row)
        self.assertEqual(row["five_hour_percent"], 20)


class PruneTests(unittest.TestCase):
    def test_removes_old_rows(self) -> None:
        h = _in_memory_history()
        now = datetime.now(timezone.utc)
        h.record(_make_snapshot(fetched_at=now - timedelta(days=10)))
        h.record(_make_snapshot(fetched_at=now))
        h.prune(keep_days=7)
        rows = h.recent(hours=24 * 20)
        # Only the recent row should remain
        self.assertEqual(len(rows), 1)

    def test_retains_rows_within_window(self) -> None:
        h = _in_memory_history()
        now = datetime.now(timezone.utc)
        for i in range(5):
            h.record(_make_snapshot(fetched_at=now - timedelta(days=i)))
        h.prune(keep_days=7)
        rows = h.recent(hours=24 * 7)
        self.assertEqual(len(rows), 5)


class RecentTests(unittest.TestCase):
    def test_returns_empty_list_when_no_data(self) -> None:
        h = _in_memory_history()
        self.assertEqual(h.recent(hours=24), [])

    def test_returns_single_row(self) -> None:
        h = _in_memory_history()
        now = datetime.now(timezone.utc)
        h.record(_make_snapshot(five_pct=55, fetched_at=now))
        rows = h.recent(hours=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["five_hour_percent"], 55)

    def test_downsampling_includes_first_and_last(self) -> None:
        h = _in_memory_history()
        now = datetime.now(timezone.utc)
        # Need total >= 2 * max_points so that stride = total // max_points >= 2
        # and downsampling actually kicks in
        n = 600
        for i in range(n):
            h.record(_make_snapshot(five_pct=i % 100, fetched_at=now - timedelta(seconds=n - i)))
        rows = h.recent(hours=24, max_points=300)
        # stride = 600 // 300 = 2  →  ~300 sampled rows + always-include-last = 301 max
        self.assertLessEqual(len(rows), 301)
        self.assertGreater(len(rows), 0)

    def test_downsampling_stride_one_returns_all_rows(self) -> None:
        """When total <= max_points, stride is 1 and every row is returned."""
        h = _in_memory_history()
        now = datetime.now(timezone.utc)
        for i in range(10):
            h.record(_make_snapshot(fetched_at=now - timedelta(minutes=10 - i)))
        rows = h.recent(hours=1, max_points=300)
        self.assertEqual(len(rows), 10)

    def test_excludes_rows_outside_window(self) -> None:
        h = _in_memory_history()
        now = datetime.now(timezone.utc)
        h.record(_make_snapshot(five_pct=99, fetched_at=now - timedelta(hours=25)))
        h.record(_make_snapshot(five_pct=1, fetched_at=now))
        rows = h.recent(hours=24)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["five_hour_percent"], 1)


class StatsTests(unittest.TestCase):
    def test_returns_defaults_on_empty_db(self) -> None:
        h = _in_memory_history()
        s = h.stats()
        self.assertEqual(s["average_daily_usage"], 0)
        self.assertEqual(s["peak_utilization"], 0)
        self.assertEqual(s["trend"], "steady")
        self.assertEqual(s["points_24h"], [])
        self.assertEqual(s["points_7d"], [])

    def test_peak_is_max_in_last_24h(self) -> None:
        h = _in_memory_history()
        now = datetime.now(timezone.utc)
        for pct in (10, 50, 90, 30):
            h.record(_make_snapshot(five_pct=pct, fetched_at=now - timedelta(minutes=1)))
        s = h.stats()
        self.assertEqual(s["peak_utilization"], 90)

    def test_trend_rising_when_usage_increases(self) -> None:
        h = _in_memory_history()
        now = datetime.now(timezone.utc)
        # First half: low usage; second half: high usage
        for i in range(10):
            h.record(_make_snapshot(five_pct=5, fetched_at=now - timedelta(days=6, hours=i)))
        for i in range(10):
            h.record(_make_snapshot(five_pct=80, fetched_at=now - timedelta(hours=i)))
        s = h.stats()
        self.assertEqual(s["trend"], "rising")

    def test_trend_falling_when_usage_decreases(self) -> None:
        h = _in_memory_history()
        now = datetime.now(timezone.utc)
        for i in range(10):
            h.record(_make_snapshot(five_pct=80, fetched_at=now - timedelta(days=6, hours=i)))
        for i in range(10):
            h.record(_make_snapshot(five_pct=5, fetched_at=now - timedelta(hours=i)))
        s = h.stats()
        self.assertEqual(s["trend"], "falling")


if __name__ == "__main__":
    unittest.main()
