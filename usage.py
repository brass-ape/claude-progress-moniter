from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class UsageSnapshot:
    five_hour_percent: int
    weekly_percent: int
    five_hour_reset: datetime | None
    weekly_reset: datetime | None
    five_hour_remaining: str
    weekly_remaining: str
    five_hour_reset_label: str
    weekly_reset_label: str
    clock_time: str
    clock_date: str
    fetched_at: datetime
    api_latency_ms: int


def parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def clamp_percent(value: Any) -> int:
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return 0


def percent_from_bucket(bucket: dict[str, Any]) -> int:
    for key in ("utilization", "percent", "percentage", "usage_percent"):
        if key in bucket:
            return clamp_percent(bucket.get(key))

    # `or` would treat a legitimate 0 as "missing" and fall through to the other
    # field name, so check presence explicitly instead.
    used = bucket["used"] if "used" in bucket else bucket.get("tokens_used")
    limit = bucket["limit"] if "limit" in bucket else bucket.get("tokens_limit")
    try:
        if limit:
            return clamp_percent((float(used) / float(limit)) * 100)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0
    return 0


def first_present(mapping: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return None


def format_remaining(reset_at: datetime | None, now: datetime) -> str:
    if reset_at is None:
        return "--"

    seconds = max(0, int((reset_at - now).total_seconds()))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60

    if days:
        return f"{days}d{hours:02d}h"
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m"


def format_reset_label(reset_at: datetime | None, tz: ZoneInfo) -> str:
    if reset_at is None:
        return "--:--"
    return reset_at.astimezone(tz).strftime("%H:%M")


def parse_usage_payload(payload: dict[str, Any], timezone_name: str, api_latency_ms: int) -> UsageSnapshot:
    tz = ZoneInfo(timezone_name)
    now = datetime.now(timezone.utc)
    local_now = now.astimezone(tz)

    five_hour = first_present(payload, ("five_hour", "fiveHour", "five_hour_usage")) or {}
    weekly = first_present(payload, ("seven_day", "weekly", "week", "weekly_usage")) or {}

    five_hour_reset = parse_iso_datetime(
        first_present(five_hour, ("resets_at", "reset_at", "resetTime", "reset_time"))
    )
    weekly_reset = parse_iso_datetime(
        first_present(weekly, ("resets_at", "reset_at", "resetTime", "reset_time"))
    )

    return UsageSnapshot(
        five_hour_percent=percent_from_bucket(five_hour),
        weekly_percent=percent_from_bucket(weekly),
        five_hour_reset=five_hour_reset,
        weekly_reset=weekly_reset,
        five_hour_remaining=format_remaining(five_hour_reset, now),
        weekly_remaining=format_remaining(weekly_reset, now),
        five_hour_reset_label=format_reset_label(five_hour_reset, tz),
        weekly_reset_label=format_reset_label(weekly_reset, tz),
        clock_time=local_now.strftime("%H:%M:%S"),
        clock_date=local_now.strftime("%a %-d %b"),
        fetched_at=now,
        api_latency_ms=api_latency_ms,
    )
