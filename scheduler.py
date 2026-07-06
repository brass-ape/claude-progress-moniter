from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from client import ClaudeUsageClient
from database import connect_database
from history import UsageHistory
from logger import log
from serial_display import SerialDisplay
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
}

VALID_MODES = {"AUTO", "FIVE", "WEEK", "CLOCK", "STATUS"}


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


class ClaudeMonitorApp:
    def __init__(self, config_path: str = "config.json") -> None:
        self.config = load_config(config_path)
        self.state = AppState(started_at=time.monotonic())
        self.lock = threading.Lock()
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

    def manual_refresh(self) -> None:
        self.fetch_once()

    def status(self) -> dict[str, Any]:
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
                "history": self.history.stats(),
            }
            payload["usage"] = snapshot_to_json(snapshot) if snapshot else None
            return payload

    def _format_epoch(self, epoch: float | None) -> str | None:
        if epoch is None:
            return None
        return datetime.fromtimestamp(epoch, timezone.utc).isoformat()

    def _lcd_state_locked(self) -> str:
        if not self.state.display_on:
            return "OFF"
        if self.state.api_status in {"stale", "using_cache"}:
            return "CACHE"
        if self.state.api_status != "ok":
            return "ERR"
        if self.state.last_snapshot and self.state.last_snapshot.five_hour_percent >= int(self.config["warning_threshold"]):
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
        self.display.send_snapshot(state, mode, snapshot, display_on)

    def fetch_once(self) -> None:
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
                packet = self._packet_locked()
            log(
                "Fetched usage: "
                f"5H {snapshot.five_hour_percent}% / week {snapshot.weekly_percent}% "
                f"latency {snapshot.api_latency_ms}ms"
            )
            self._send_packet(packet)
        except Exception as exc:
            error = str(exc)
            with self.lock:
                self.state.last_error = error
                self.state.oauth_status = "invalid" if "401" in error or "403" in error else "unknown"
                self.state.internet_status = "offline" if "Connection" in error or "timed out" in error else "unknown"
                if self.state.last_success_time is None:
                    self.state.api_status = "error"
                else:
                    age = time.monotonic() - self.state.last_success_time
                    self.state.api_status = "stale" if age > int(self.config["stale_after_seconds"]) else "using_cache"
                packet = self._packet_locked()
            log(f"Fetch failed: {error}")
            self._send_packet(packet)

    def run(self) -> None:
        threading.Thread(
            target=run_server,
            args=(self, self.config["web_host"], int(self.config["web_port"])),
            daemon=True,
        ).start()
        log(f"Dashboard on http://<this-device>:{self.config['web_port']}/")
        self.display.connect()

        last_fetch = 0.0
        while True:
            now = time.monotonic()
            if now - last_fetch >= int(self.config["refresh_seconds"]):
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
            time.sleep(1)


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
