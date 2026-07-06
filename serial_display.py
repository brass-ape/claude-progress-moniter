from __future__ import annotations

import threading
import time

import serial

from logger import log, warn, error
from usage import UsageSnapshot


class SerialDisplay:
    def __init__(self, port: str, baud: int, reconnect_seconds: int, heartbeat_seconds: int) -> None:
        self.port = port
        self.baud = baud
        self.reconnect_seconds = reconnect_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.connection: serial.Serial | None = None
        self.connected = False
        self.last_error: str | None = None
        self.last_line: str | None = None
        self.last_sent_at = 0.0
        self.last_sys_line: str | None = None
        self.last_sys_sent_at = 0.0
        self._connect_lock = threading.Lock()
        self._reconnect_thread: threading.Thread | None = None

    def connect(self) -> None:
        """Start the serial connection loop in a background thread so the caller is not blocked."""
        self._start_connect_thread()

    def _start_connect_thread(self) -> None:
        """(Re)start the background reconnect loop if one isn't already running.

        Without this guard, a device that drops out mid-session — where the inline
        retry in _write() also fails — would be abandoned forever: previously the
        background loop only ran once at startup and exited as soon as it connected,
        so nothing else ever tried to reopen the port.
        """
        with self._connect_lock:
            if self._reconnect_thread is not None and self._reconnect_thread.is_alive():
                return
            self._reconnect_thread = threading.Thread(target=self._connect_loop, daemon=True)
            self._reconnect_thread.start()

    def _connect_loop(self) -> None:
        while self.connection is None:
            try:
                self.connection = serial.Serial(self.port, self.baud, timeout=1)
                time.sleep(2)
                self.connected = True
                self.last_error = None
                log("Serial connected")
            except Exception as exc:
                self.connected = False
                self.last_error = str(exc)
                warn(f"Retrying serial... {exc}")
                time.sleep(self.reconnect_seconds)

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
        self.connection = None
        self.connected = False
        self.last_line = None

    def send_line(self, line: str, force: bool = False) -> None:
        now = time.monotonic()
        if not force and line == self.last_line and now - self.last_sent_at < self.heartbeat_seconds:
            return
        if self._write(line):
            self.last_line = line
            self.last_sent_at = now

    def send_sys_line(self, line: str, force: bool = False) -> None:
        """Like send_line() but tracks its own dedup/heartbeat state, so the
        S1 (system-info) packet doesn't get resent on every tick just because
        the unrelated V1 packet changed (e.g. the clock ticking every second)."""
        now = time.monotonic()
        if not force and line == self.last_sys_line and now - self.last_sys_sent_at < self.heartbeat_seconds:
            return
        if self._write(line):
            self.last_sys_line = line
            self.last_sys_sent_at = now

    def _write(self, line: str) -> bool:
        if self.connection is None:
            # Not yet connected — kick off (or leave running) the background
            # reconnect loop and skip silently rather than blocking the main loop
            self._start_connect_thread()
            return False

        for attempt in range(2):
            try:
                if self.connection is None:
                    raise OSError("No serial connection")
                self.connection.write((line + "\n").encode("ascii", errors="replace"))
                self.connected = True
                self.last_error = None
                log(f"Sent: {line}")
                return True
            except Exception as exc:
                error(f"Serial write failed (attempt {attempt + 1}): {exc}")
                self.last_error = str(exc)
                self.close()
                if attempt == 0:
                    # Inline reconnect before second attempt
                    try:
                        self.connection = serial.Serial(self.port, self.baud, timeout=1)
                        time.sleep(2)
                        self.connected = True
                        self.last_error = None
                        log("Serial reconnected")
                    except Exception as reconnect_exc:
                        self.last_error = str(reconnect_exc)
                        error(f"Serial reconnect failed: {reconnect_exc}")
                        break

        # Both the write and the inline reconnect attempt failed. Fall back to the
        # background reconnect loop instead of leaving the display dead forever.
        if self.connection is None:
            self._start_connect_thread()
        return False

    def send_snapshot(self, state: str, mode: str, snapshot: UsageSnapshot | None, display_on: bool) -> None:
        if not display_on:
            self.send_line(f"V1,OFF,{mode},0,--,0,--,--:--:--,--")
            return
        if snapshot is None:
            values = ("0", "--", "0", "--", "--:--:--", "--")
        else:
            values = (
                str(snapshot.five_hour_percent),
                snapshot.five_hour_remaining,
                str(snapshot.weekly_percent),
                snapshot.weekly_remaining,
                snapshot.clock_time,
                snapshot.clock_date,
            )

        line = ",".join(("V1", state, mode, *values))
        self.send_line(line)
