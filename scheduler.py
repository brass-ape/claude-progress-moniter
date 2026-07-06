from __future__ import annotations

import json
import threading
import time
import dataclasses
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any

import requests

from client import ClaudeUsageClient
from database import connect_database
from history import UsageHistory
from logger import log, warn, error
from serial_display import SerialDisplay
from sysinfo import SystemMetrics, SysInfoSampler, format_metric_lines, next_metric_index
from usage import UsageSnapshot, parse_usage_payload
from web import run_server


BASE_DIR = Path(__file__).resolve().parent

DEFAULT_CONFIG = {
    "credentials_path": "~/.claude/.credentials.json",
    "usage_url": "https://api.anthropic.com/api/oauth/usage",
    "timezone": "Europe/London",
    "serial_port": "/dev/ttyACM0",
    "baud": 115200,
    "web_host": "0.0.0.0",
    "web_port": 8090,
    "database_path": "usage_history.sqlite3",
    "refresh_seconds": 60,
    "stale_after_seconds": 300,
    "heartbeat_seconds": 30,
    "serial_reconnect_seconds": 3,
    "warning_threshold": 80,
    "request_timeout_seconds": 10,
    "prune_days": 7,
    "sysinfo_metrics": ["cpu", "ram", "gpu", "disk"],
    "sysinfo_ram_mode": "percent",
    "sysinfo_disk_mode": "percent",
    "sysinfo_rotate_seconds": 4,
    "sysinfo_gpu_sample_seconds": 5,
}

VALID_MODES = {"AUTO", "FIVE", "WEEK", "CLOCK", "STATUS", "SYS"}
SYSINFO_METRIC_NAMES = {"cpu", "ram", "gpu", "disk", "net"}

# Prune the database once every 24 hours
_PRUNE_INTERVAL_SECONDS = 86400


@dataclass
class AppState:
    started_at: float
    display_on: bool = True
    display_mode: str = "AUTO"
    lcd_state: str = "waiting"
    oauth_status: str = "unknown"
    internet_status: str = "unknown"
    api_status: str = "waiting"
    last_error: str | None = None
    last_success_time: float | None = None
    last_snapshot: UsageSnapshot | None = None
    retry_after: float = 0.0  # monotonic time before which fetches are suppressed
    sys_metric_index: int = 0
    sys_last_rotate: float = 0.0
    sys_line0: str = "System"
    sys_line1: str = ""
    last_sys_metrics: SystemMetrics | None = None


class ClaudeMonitorApp:
    def __init__(self, config_path: str = "config.json") -> None:
        self.config = load_config(config_path)
        self.state = AppState(started_at=time.monotonic())
        self.lock = threading.Lock()
        self._fetch_lock = threading.Lock()  # prevents overlapping fetch_once() calls
        self.client = ClaudeUsageClient(
            self.config["credentials_path"],
            self.config["usage_url"],
            int(self.config["request_timeout_seconds"]),
        )
        database_path = BASE_DIR / self.config["database_path"]
        self.history = UsageHistory(connect_database(str(database_path)))
        self.display = SerialDisplay(
            self.config["serial_port"],
            int(self.config["baud"]),
            int(self.config["serial_reconnect_seconds"]),
            int(self.config["heartbeat_seconds"]),
        )
        self.sysinfo = SysInfoSampler()
        self._seed_from_db()

    def _seed_from_db(self) -> None:
        """Pre-populate state from the most recent DB row so the display
        shows real numbers immediately on startup, before the first fetch."""
        row = self.history.latest_row()
        if row is None:
            return
        try:
            snapshot = _snapshot_from_row(row, self.config["timezone"])
            self.state.last_snapshot = snapshot
            self.state.api_status = "using_cache"
            log("Seeded initial state from DB cache")
        except Exception as exc:
            warn(f"Could not seed from DB: {exc}")

    def set_display(self, on: bool) -> None:
        with self.lock:
            self.state.display_on = on
            packet = self._packet_locked()
        self._send_packet(packet, force=True)

    def set_display_mode(self, mode: str) -> None:
        normalized = mode.upper()
        if normalized not in VALID_MODES:
            normalized = "AUTO"
        with self.lock:
            self.state.display_mode = normalized
            packet = self._packet_locked()
        self._send_packet(packet, force=True)

    def get_settings(self) -> dict[str, Any]:
        with self.lock:
            return {
                "warning_threshold": int(self.config["warning_threshold"]),
                "refresh_seconds": int(self.config["refresh_seconds"]),
                "stale_after_seconds": int(self.config["stale_after_seconds"]),
                "sysinfo_metrics": list(self.config["sysinfo_metrics"]),
                "sysinfo_ram_mode": self.config["sysinfo_ram_mode"],
                "sysinfo_disk_mode": self.config["sysinfo_disk_mode"],
            }

    def update_settings(self, body: dict[str, Any]) -> None:
        updated: dict[str, Any] = {}
        if "warning_threshold" in body:
            val = int(body["warning_threshold"])
            if 1 <= val <= 99:
                updated["warning_threshold"] = val
        if "refresh_seconds" in body:
            val = int(body["refresh_seconds"])
            if 10 <= val <= 3600:
                updated["refresh_seconds"] = val
        if "stale_after_seconds" in body:
            val = int(body["stale_after_seconds"])
            if 60 <= val <= 86400:
                updated["stale_after_seconds"] = val
        if "sysinfo_metrics" in body and isinstance(body["sysinfo_metrics"], list):
            cleaned: list[str] = []
            for item in body["sysinfo_metrics"]:
                name = str(item).lower()
                if name in SYSINFO_METRIC_NAMES and name not in cleaned:
                    cleaned.append(name)
            updated["sysinfo_metrics"] = cleaned
        if body.get("sysinfo_ram_mode") in {"percent", "used_total"}:
            updated["sysinfo_ram_mode"] = body["sysinfo_ram_mode"]
        if body.get("sysinfo_disk_mode") in {"percent", "used_total", "io_speed"}:
            updated["sysinfo_disk_mode"] = body["sysinfo_disk_mode"]
        if not updated:
            return
        with self.lock:
            self.config.update(updated)
        # Persist to config.json so settings survive a restart
        config_path = BASE_DIR / "config.json"
        try:
            existing: dict = {}
            if config_path.exists():
                existing = json.loads(config_path.read_text())
            existing.update(updated)
            config_path.write_text(json.dumps(existing, indent=2))
            log(f"Settings updated: {updated}")
        except Exception as exc:
            warn(f"Could not persist settings: {exc}")

    def manual_refresh(self) -> None:
        self.fetch_once()

    def status(self) -> dict[str, Any]:
        # Snapshot mutable state under lock, then do DB work outside it
        with self.lock:
            snapshot = self.state.last_snapshot
            payload = {
                "display_on": self.state.display_on,
                "display_mode": self.state.display_mode,
                "lcd_state": self.state.lcd_state,
                "oauth_status": self.state.oauth_status,
                "internet_status": self.state.internet_status,
                "api_status": self.state.api_status,
                "last_error": self.state.last_error,
                "last_success": self._format_epoch(self.state.last_success_time),
                "uptime_seconds": int(time.monotonic() - self.state.started_at),
                "arduino_connected": self.display.connected,
                "arduino_error": self.display.last_error,
                "rate_limit_seconds": max(0, int(self.state.retry_after - time.monotonic())),
            }
            payload["usage"] = snapshot_to_json(snapshot) if snapshot else None
            payload["sysinfo"] = {
                "line0": self.state.sys_line0,
                "line1": self.state.sys_line1,
                **(dataclasses.asdict(self.state.last_sys_metrics) if self.state.last_sys_metrics else {}),
            }

        # DB queries run outside the lock so they don't stall the display loop
        payload["history"] = self.history.stats()
        return payload

    def _format_epoch(self, epoch: float | None) -> str | None:
        if epoch is None:
            return None
        return datetime.fromtimestamp(epoch, timezone.utc).isoformat()

    def _lcd_state_locked(self) -> str:
        if not self.state.display_on:
            return "OFF"
        if self.state.api_status in {"stale", "using_cache", "rate_limited"}:
            return "CACHE"
        if self.state.api_status != "ok":
            return "ERR"
        threshold = int(self.config["warning_threshold"])
        if self.state.last_snapshot and (
            self.state.last_snapshot.five_hour_percent >= threshold
            or self.state.last_snapshot.weekly_percent >= threshold
        ):
            return "WARN"
        return "OK"

    def _packet_locked(self) -> tuple[str, str, UsageSnapshot | None, bool]:
        self.state.lcd_state = self._lcd_state_locked()
        return (
            self.state.lcd_state,
            self.state.display_mode,
            self.state.last_snapshot,
            self.state.display_on,
        )

    def _send_packet(self, packet: tuple[str, str, UsageSnapshot | None, bool], force: bool = False) -> None:
        state, mode, snapshot, display_on = packet
        if force:
            self.display.last_line = None
        # Refresh clock fields every call so the LCD is never more than a second behind
        if snapshot is not None:
            now = datetime.now(ZoneInfo(self.config["timezone"]))
            snapshot = dataclasses.replace(
                snapshot,
                clock_time=now.strftime("%H:%M:%S"),
                clock_date=now.strftime("%a %-d %b"),
            )
        self.display.send_snapshot(state, mode, snapshot, display_on)

    def _send_sysinfo(self, now: float) -> None:
        metrics = self.sysinfo.sample(now, int(self.config["sysinfo_gpu_sample_seconds"]))
        with self.lock:
            enabled = self.config["sysinfo_metrics"]
            if now - self.state.sys_last_rotate >= int(self.config["sysinfo_rotate_seconds"]):
                self.state.sys_last_rotate = now
                self.state.sys_metric_index += 1
            name = enabled[next_metric_index(enabled, self.state.sys_metric_index)] if enabled else None
            self.state.sys_line0, self.state.sys_line1 = format_metric_lines(
                name, metrics, self.config["sysinfo_ram_mode"], self.config["sysinfo_disk_mode"]
            )
            self.state.last_sys_metrics = metrics
            line0, line1 = self.state.sys_line0, self.state.sys_line1
        self.display.send_sys_line(f"S1,{line0},{line1}")

    def fetch_once(self) -> None:
        with self.lock:
            if time.monotonic() < self.state.retry_after:
                log("Rate-limited, skipping fetch")
                return
        if not self._fetch_lock.acquire(blocking=False):
            log("Fetch already in progress, skipping")
            return
        try:
            try:
                payload, latency_ms = self.client.fetch_usage()
                snapshot = parse_usage_payload(payload, self.config["timezone"], latency_ms)
                self.history.record(snapshot)
                with self.lock:
                    self.state.last_snapshot = snapshot
                    self.state.last_success_time = time.monotonic()
                    self.state.api_status = "ok"
                    self.state.oauth_status = "ok"
                    self.state.internet_status = "ok"
                    self.state.last_error = None
                    self.state.retry_after = 0.0
                    packet = self._packet_locked()
                log(
                    "Fetched usage: "
                    f"5H {snapshot.five_hour_percent}% / week {snapshot.weekly_percent}% "
                    f"latency {snapshot.api_latency_ms}ms"
                )
                self._send_packet(packet)
            except requests.HTTPError as exc:
                status_code = exc.response.status_code if exc.response is not None else None
                if status_code == 429:
                    # Honour Retry-After header if present, otherwise back off 5 minutes.
                    # Per HTTP spec the header may also be an HTTP-date rather than a
                    # delay in seconds — fall back to the default instead of crashing.
                    try:
                        retry_secs = int(exc.response.headers.get("Retry-After", 300))
                    except (TypeError, ValueError):
                        retry_secs = 300
                    warn(f"Rate limited (429), backing off {retry_secs}s")
                    with self.lock:
                        self.state.retry_after = time.monotonic() + retry_secs
                        self.state.last_error = str(exc)
                        self.state.oauth_status = "ok"
                        self.state.internet_status = "ok"
                        # Show cached data regardless of age — the API is reachable,
                        # just asking us to wait. "rate_limited" maps to CACHE on the LCD.
                        self.state.api_status = "rate_limited"
                        packet = self._packet_locked()
                    self._send_packet(packet)
                else:
                    oauth = "invalid" if status_code in (401, 403) else "unknown"
                    self._record_fetch_error(str(exc), oauth_status=oauth, internet_status="ok")
            except (requests.ConnectionError, requests.Timeout) as exc:
                self._record_fetch_error(str(exc), oauth_status="unknown", internet_status="offline")
            except Exception as exc:
                self._record_fetch_error(str(exc), oauth_status="unknown", internet_status="unknown")
        finally:
            self._fetch_lock.release()

    def _record_fetch_error(self, error_message: str, oauth_status: str, internet_status: str) -> None:
        with self.lock:
            self.state.last_error = error_message
            self.state.oauth_status = oauth_status
            self.state.internet_status = internet_status
            if self.state.last_success_time is None:
                self.state.api_status = "error"
            else:
                age = time.monotonic() - self.state.last_success_time
                self.state.api_status = "stale" if age > int(self.config["stale_after_seconds"]) else "using_cache"
            packet = self._packet_locked()
        error(f"Fetch failed: {error_message}")
        self._send_packet(packet)

    def run(self) -> None:
        threading.Thread(
            target=run_server,
            args=(self, self.config["web_host"], int(self.config["web_port"])),
            daemon=True,
        ).start()
        log(f"Dashboard on http://<this-device>:{self.config['web_port']}/")
        self.display.connect()

        # None (rather than 0.0) guarantees the very first iteration always fetches.
        # time.monotonic() reflects uptime on Linux, so right after boot (e.g. a
        # systemd unit starting early) it can be smaller than refresh_seconds —
        # with a 0.0 sentinel that would skip the first fetch and leave api_status
        # at its "waiting" default (rendered as ERR/"API Offline") for no reason.
        last_fetch: float | None = None
        last_prune = time.monotonic()  # defer first prune until the interval has elapsed
        while True:
            now = time.monotonic()

            if now - last_prune >= _PRUNE_INTERVAL_SECONDS:
                last_prune = now
                try:
                    self.history.prune(keep_days=int(self.config["prune_days"]))
                except Exception as exc:
                    error(f"DB prune failed: {exc}")

            if last_fetch is None or now - last_fetch >= int(self.config["refresh_seconds"]):
                last_fetch = now
                self.fetch_once()

            with self.lock:
                if (
                    self.state.last_success_time is not None
                    and now - self.state.last_success_time > int(self.config["stale_after_seconds"])
                    and self.state.api_status == "ok"
                ):
                    self.state.api_status = "stale"
                packet = self._packet_locked()
            self._send_packet(packet)
            self._send_sysinfo(now)
            time.sleep(1)


def _snapshot_from_row(row: dict[str, Any], timezone_name: str) -> UsageSnapshot:
    """Reconstruct a UsageSnapshot from a raw DB row."""
    from usage import parse_iso_datetime, format_remaining, format_reset_label
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(timezone_name)
    now = datetime.now(timezone.utc)
    local_now = now.astimezone(tz)

    five_hour_reset = parse_iso_datetime(row.get("five_hour_reset"))
    weekly_reset = parse_iso_datetime(row.get("weekly_reset"))

    return UsageSnapshot(
        five_hour_percent=int(row["five_hour_percent"]),
        weekly_percent=int(row["weekly_percent"]),
        five_hour_reset=five_hour_reset,
        weekly_reset=weekly_reset,
        five_hour_remaining=format_remaining(five_hour_reset, now),
        weekly_remaining=format_remaining(weekly_reset, now),
        five_hour_reset_label=format_reset_label(five_hour_reset, tz),
        weekly_reset_label=format_reset_label(weekly_reset, tz),
        clock_time=local_now.strftime("%H:%M:%S"),
        clock_date=local_now.strftime("%a %-d %b"),
        fetched_at=now,
        api_latency_ms=int(row["api_latency_ms"]),
    )


def snapshot_to_json(snapshot: UsageSnapshot) -> dict[str, Any]:
    data = asdict(snapshot)
    data["fetched_at"] = snapshot.fetched_at.isoformat()
    data["five_hour_reset"] = snapshot.five_hour_reset.isoformat() if snapshot.five_hour_reset else None
    data["weekly_reset"] = snapshot.weekly_reset.isoformat() if snapshot.weekly_reset else None
    return data


def load_config(config_path: str) -> dict[str, Any]:
    path = BASE_DIR / config_path
    config = dict(DEFAULT_CONFIG)
    if path.exists():
        config.update(json.loads(path.read_text()))
    return config


def main() -> None:
    ClaudeMonitorApp().run()


if __name__ == "__main__":
    main()
