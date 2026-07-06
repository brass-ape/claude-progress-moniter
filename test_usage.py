from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from usage import (
    clamp_percent,
    format_remaining,
    format_reset_label,
    parse_iso_datetime,
    parse_usage_payload,
    percent_from_bucket,
)


class ClampPercentTests(unittest.TestCase):
    def test_clamps_range(self) -> None:
        self.assertEqual(clamp_percent(150), 100)
        self.assertEqual(clamp_percent(-10), 0)
        self.assertEqual(clamp_percent(42.6), 43)

    def test_invalid_input_defaults_to_zero(self) -> None:
        self.assertEqual(clamp_percent(None), 0)
        self.assertEqual(clamp_percent("not a number"), 0)


class PercentFromBucketTests(unittest.TestCase):
    def test_uses_direct_utilization_field(self) -> None:
        self.assertEqual(percent_from_bucket({"utilization": 37}), 37)
        self.assertEqual(percent_from_bucket({"percent": 12}), 12)

    def test_computes_from_used_and_limit(self) -> None:
        self.assertEqual(percent_from_bucket({"used": 25, "limit": 100}), 25)

    def test_missing_or_zero_limit_defaults_to_zero(self) -> None:
        self.assertEqual(percent_from_bucket({}), 0)
        self.assertEqual(percent_from_bucket({"used": 5, "limit": 0}), 0)


class FormatRemainingTests(unittest.TestCase):
    def test_none_reset(self) -> None:
        self.assertEqual(format_remaining(None, datetime.now(timezone.utc)), "--")

    def test_days_hours_minutes(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        reset = now + timedelta(days=4, hours=12, minutes=30)
        self.assertEqual(format_remaining(reset, now), "4d12h")

    def test_hours_minutes_only(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        reset = now + timedelta(hours=2, minutes=13)
        self.assertEqual(format_remaining(reset, now), "2h13m")

    def test_minutes_only(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        reset = now + timedelta(minutes=45)
        self.assertEqual(format_remaining(reset, now), "45m")

    def test_past_reset_clamps_to_zero(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        reset = now - timedelta(minutes=5)
        self.assertEqual(format_remaining(reset, now), "0m")


class FormatResetLabelTests(unittest.TestCase):
    def test_none_reset(self) -> None:
        self.assertEqual(format_reset_label(None, ZoneInfo("UTC")), "--:--")

    def test_formats_in_target_timezone(self) -> None:
        reset = datetime(2026, 7, 6, 15, 59, tzinfo=timezone.utc)
        self.assertEqual(format_reset_label(reset, ZoneInfo("Europe/London")), "16:59")


class ParseIsoDatetimeTests(unittest.TestCase):
    def test_parses_z_suffix(self) -> None:
        result = parse_iso_datetime("2026-07-06T15:59:00Z")
        self.assertEqual(result, datetime(2026, 7, 6, 15, 59, tzinfo=timezone.utc))

    def test_invalid_or_missing_returns_none(self) -> None:
        self.assertIsNone(parse_iso_datetime(None))
        self.assertIsNone(parse_iso_datetime(""))
        self.assertIsNone(parse_iso_datetime("not a date"))


class ParseUsagePayloadTests(unittest.TestCase):
    def test_parses_full_payload(self) -> None:
        payload = {
            "five_hour": {"utilization": 42, "resets_at": "2026-07-06T15:59:00Z"},
            "weekly": {"utilization": 18, "resets_at": "2026-07-11T00:00:00Z"},
        }
        snapshot = parse_usage_payload(payload, "Europe/London", api_latency_ms=123)
        self.assertEqual(snapshot.five_hour_percent, 42)
        self.assertEqual(snapshot.weekly_percent, 18)
        self.assertEqual(snapshot.api_latency_ms, 123)
        self.assertEqual(snapshot.five_hour_reset_label, "16:59")

    def test_missing_buckets_default_gracefully(self) -> None:
        snapshot = parse_usage_payload({}, "UTC", api_latency_ms=0)
        self.assertEqual(snapshot.five_hour_percent, 0)
        self.assertEqual(snapshot.weekly_percent, 0)
        self.assertEqual(snapshot.five_hour_remaining, "--")
        self.assertEqual(snapshot.five_hour_reset_label, "--:--")


if __name__ == "__main__":
    unittest.main()
