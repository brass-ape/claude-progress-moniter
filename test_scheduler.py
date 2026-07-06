from __future__ import annotations

import time
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from scheduler import ClaudeMonitorApp, _snapshot_from_row, snapshot_to_json
from usage import UsageSnapshot


def _make_snapshot(five_pct: int = 50, week_pct: int = 20) -> UsageSnapshot:
    now = datetime.now(timezone.utc)
    return UsageSnapshot(
        five_hour_percent=five_pct,
        weekly_percent=week_pct,
        five_hour_reset=None,
        weekly_reset=None,
        five_hour_remaining="2h00m",
        weekly_remaining="3d12h",
        five_hour_reset_label="14:00",
        weekly_reset_label="Sat",
        clock_time="12:00:00",
        clock_date="Mon 6 Jul",
        fetched_at=now,
        api_latency_ms=50,
    )


def _make_app() -> ClaudeMonitorApp:
    """Return a ClaudeMonitorApp with all I/O dependencies mocked out."""
    with (
        patch("scheduler.ClaudeUsageClient"),
        patch("scheduler.connect_database"),
        patch("scheduler.UsageHistory") as MockHistory,
        patch("scheduler.SerialDisplay"),
    ):
        MockHistory.return_value.latest_row.return_value = None
        MockHistory.return_value.stats.return_value = {
            "average_daily_usage": 0,
            "peak_utilization": 0,
            "trend": "steady",
            "points_24h": [],
            "points_7d": [],
        }
        app = ClaudeMonitorApp.__new__(ClaudeMonitorApp)
        # Manually initialise enough state for the tests
        from scheduler import load_config, DEFAULT_CONFIG
        import threading
        app.config = dict(DEFAULT_CONFIG)
        app.config["database_path"] = ":memory:"
        app.state = __import__("scheduler").AppState(started_at=time.monotonic())
        app.lock = threading.Lock()
        app._fetch_lock = threading.Lock()
        app.history = MockHistory.return_value
        app.display = MagicMock()
        app.display.connected = True
        app.display.last_error = None
        return app


class LcdStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = _make_app()

    def test_ok_when_below_threshold(self) -> None:
        self.app.state.display_on = True
        self.app.state.api_status = "ok"
        self.app.state.last_snapshot = _make_snapshot(five_pct=50, week_pct=20)
        self.assertEqual(self.app._lcd_state_locked(), "OK")

    def test_warn_when_above_threshold(self) -> None:
        self.app.state.display_on = True
        self.app.state.api_status = "ok"
        self.app.state.last_snapshot = _make_snapshot(five_pct=85)
        self.assertEqual(self.app._lcd_state_locked(), "WARN")

    def test_off_when_display_off(self) -> None:
        self.app.state.display_on = False
        self.assertEqual(self.app._lcd_state_locked(), "OFF")

    def test_cache_for_rate_limited(self) -> None:
        self.app.state.display_on = True
        self.app.state.api_status = "rate_limited"
        self.assertEqual(self.app._lcd_state_locked(), "CACHE")

    def test_cache_for_using_cache(self) -> None:
        self.app.state.display_on = True
        self.app.state.api_status = "using_cache"
        self.assertEqual(self.app._lcd_state_locked(), "CACHE")

    def test_err_on_error_status(self) -> None:
        self.app.state.display_on = True
        self.app.state.api_status = "error"
        self.assertEqual(self.app._lcd_state_locked(), "ERR")

    def test_danger_at_95_percent_still_warn_lcd(self) -> None:
        """LCD only has WARN/OK/ERR/CACHE/OFF — 95% still shows WARN."""
        self.app.state.display_on = True
        self.app.state.api_status = "ok"
        self.app.state.last_snapshot = _make_snapshot(five_pct=95)
        self.assertEqual(self.app._lcd_state_locked(), "WARN")


class GetSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = _make_app()

    def test_returns_expected_keys(self) -> None:
        s = self.app.get_settings()
        self.assertIn("warning_threshold", s)
        self.assertIn("refresh_seconds", s)
        self.assertIn("stale_after_seconds", s)

    def test_reflects_config_values(self) -> None:
        self.app.config["warning_threshold"] = 75
        self.app.config["refresh_seconds"] = 120
        s = self.app.get_settings()
        self.assertEqual(s["warning_threshold"], 75)
        self.assertEqual(s["refresh_seconds"], 120)


class UpdateSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = _make_app()

    def _no_config_file(self):
        """Patch out config.json write so tests don't touch the filesystem."""
        import unittest.mock
        return unittest.mock.patch("scheduler.Path.write_text")

    def test_updates_warning_threshold(self) -> None:
        with patch("pathlib.Path.write_text"), patch("pathlib.Path.exists", return_value=False):
            self.app.update_settings({"warning_threshold": 70})
        self.assertEqual(int(self.app.config["warning_threshold"]), 70)

    def test_clamps_out_of_range_threshold(self) -> None:
        original = int(self.app.config["warning_threshold"])
        with patch("pathlib.Path.write_text"), patch("pathlib.Path.exists", return_value=False):
            self.app.update_settings({"warning_threshold": 0})
            self.app.update_settings({"warning_threshold": 100})
        # Neither value should have been applied
        self.assertEqual(int(self.app.config["warning_threshold"]), original)

    def test_updates_refresh_seconds(self) -> None:
        with patch("pathlib.Path.write_text"), patch("pathlib.Path.exists", return_value=False):
            self.app.update_settings({"refresh_seconds": 120})
        self.assertEqual(int(self.app.config["refresh_seconds"]), 120)

    def test_ignores_unknown_keys(self) -> None:
        with patch("pathlib.Path.write_text"), patch("pathlib.Path.exists", return_value=False):
            self.app.update_settings({"nonexistent_key": 999})
        # Should not raise
        self.assertNotIn("nonexistent_key", self.app.config)


class SnapshotFromRowTests(unittest.TestCase):
    def test_reconstructs_snapshot(self) -> None:
        row = {
            "five_hour_percent": 42,
            "weekly_percent": 18,
            "five_hour_reset": "2026-07-06T15:59:00Z",
            "weekly_reset": "2026-07-11T00:00:00Z",
            "api_latency_ms": 77,
        }
        snapshot = _snapshot_from_row(row, "UTC")
        self.assertEqual(snapshot.five_hour_percent, 42)
        self.assertEqual(snapshot.weekly_percent, 18)
        self.assertEqual(snapshot.api_latency_ms, 77)

    def test_handles_null_reset_fields(self) -> None:
        row = {
            "five_hour_percent": 0,
            "weekly_percent": 0,
            "five_hour_reset": None,
            "weekly_reset": None,
            "api_latency_ms": 0,
        }
        snapshot = _snapshot_from_row(row, "UTC")
        self.assertIsNone(snapshot.five_hour_reset)
        self.assertIsNone(snapshot.weekly_reset)
        self.assertEqual(snapshot.five_hour_remaining, "--")


class SnapshotToJsonTests(unittest.TestCase):
    def test_serialises_datetimes_as_strings(self) -> None:
        snap = _make_snapshot()
        data = snapshot_to_json(snap)
        self.assertIsInstance(data["fetched_at"], str)
        self.assertIsNone(data["five_hour_reset"])
        self.assertIsNone(data["weekly_reset"])

    def test_contains_expected_fields(self) -> None:
        snap = _make_snapshot(five_pct=33, week_pct=11)
        data = snapshot_to_json(snap)
        self.assertEqual(data["five_hour_percent"], 33)
        self.assertEqual(data["weekly_percent"], 11)


if __name__ == "__main__":
    unittest.main()
