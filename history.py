from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from usage import UsageSnapshot

# Maximum chart points sent to the browser for each time window
_MAX_CHART_POINTS = 300


class UsageHistory:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def record(self, snapshot: UsageSnapshot) -> None:
        self.conn.execute(
            """
            INSERT INTO usage_history (
                timestamp, five_hour_percent, weekly_percent,
                five_hour_reset, weekly_reset, api_latency_ms
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.fetched_at.isoformat(),
                snapshot.five_hour_percent,
                snapshot.weekly_percent,
                snapshot.five_hour_reset.isoformat() if snapshot.five_hour_reset else None,
                snapshot.weekly_reset.isoformat() if snapshot.weekly_reset else None,
                snapshot.api_latency_ms,
            ),
        )
        self.conn.commit()

    def latest_row(self) -> dict[str, Any] | None:
        """Return the most recently recorded row, or None if the DB is empty."""
        row = self.conn.execute(
            """
            SELECT timestamp, five_hour_percent, weekly_percent,
                   five_hour_reset, weekly_reset, api_latency_ms
            FROM usage_history
            ORDER BY timestamp DESC
            LIMIT 1
            """
        ).fetchone()
        return dict(row) if row else None

    def prune(self, keep_days: int = 7) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
        self.conn.execute(
            "DELETE FROM usage_history WHERE timestamp < ?",
            (cutoff.isoformat(),),
        )
        self.conn.commit()

    def recent(self, hours: int, max_points: int = _MAX_CHART_POINTS) -> list[dict[str, Any]]:
        """Return up to max_points evenly-sampled rows from the last `hours` hours."""
        since = datetime.now(timezone.utc) - timedelta(hours=hours)

        # Count total rows in the window so we can compute a stride
        count_row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM usage_history WHERE timestamp >= ?",
            (since.isoformat(),),
        ).fetchone()
        total = count_row["n"] if count_row else 0

        if total == 0:
            return []

        stride = max(1, total // max_points)

        # Use a row-number trick to sample every Nth row
        rows = self.conn.execute(
            """
            WITH numbered AS (
                SELECT timestamp, five_hour_percent, weekly_percent, api_latency_ms,
                       ROW_NUMBER() OVER (ORDER BY timestamp) AS rn
                FROM usage_history
                WHERE timestamp >= ?
            )
            SELECT timestamp, five_hour_percent, weekly_percent, api_latency_ms
            FROM numbered
            WHERE (rn - 1) % ? = 0 OR rn = (SELECT MAX(rn) FROM numbered)
            ORDER BY timestamp ASC
            """,
            (since.isoformat(), stride),
        ).fetchall()
        return [dict(row) for row in rows]

    def stats(self) -> dict[str, Any]:
        since_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        since_7d = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

        # Aggregates computed in SQL to avoid pulling thousands of rows into Python
        agg = self.conn.execute(
            """
            SELECT
                ROUND(AVG(five_hour_percent), 1) AS average_daily,
                MAX(five_hour_percent)            AS peak
            FROM usage_history
            WHERE timestamp >= ?
            """,
            (since_24h,),
        ).fetchone()
        average_daily = agg["average_daily"] or 0
        peak = agg["peak"] or 0

        # Trend: compare average of first vs second half of the 7-day window using SQL
        trend_row = self.conn.execute(
            """
            WITH numbered AS (
                SELECT five_hour_percent,
                       ROW_NUMBER() OVER (ORDER BY timestamp) AS rn,
                       COUNT(*) OVER ()                       AS total
                FROM usage_history
                WHERE timestamp >= ?
            )
            SELECT
                AVG(CASE WHEN rn <= total / 2 THEN five_hour_percent END) AS first_half,
                AVG(CASE WHEN rn >  total / 2 THEN five_hour_percent END) AS second_half,
                COUNT(*) AS total
            FROM numbered
            """,
            (since_7d,),
        ).fetchone()

        trend = "steady"
        if trend_row and trend_row["total"] >= 4:
            first_half = trend_row["first_half"] or 0
            second_half = trend_row["second_half"] or 0
            if second_half > first_half + 5:
                trend = "rising"
            elif second_half < first_half - 5:
                trend = "falling"

        # Chart data: sampled to avoid sending thousands of rows per poll
        day_rows = self.recent(24)
        week_rows = self.recent(24 * 7)

        return {
            "average_daily_usage": average_daily,
            "peak_utilization": peak,
            "trend": trend,
            "points_24h": day_rows,
            "points_7d": week_rows,
        }
