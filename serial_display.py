from __future__ import annotations

import threading
import time

import serial

from logger import log
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
        self._connect_lock = threading.Lock()

    def connect(self) -> None:
        """Start the serial connection loop in a background thread so the caller is not blocked."""
        threading.Thread(target=self._connect_loop, daemon=True).start()

    def _connect_loop(self) -> None:
        with self._connect_lock:
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
                    log(f"Retrying serial... {exc}")
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

        if self.connection is None:
            # Not yet connected — skip silently rather than blocking the main loop
            return

        for attempt in range(2):
            try:
                if self.connection is None:
                    raise OSError("No serial connection")
                self.connection.write((line + "\n").encode("ascii", errors="replace"))
                self.last_line = line
                self.last_sent_at = now
                self.connected = True
                self.last_error = None
                log(f"Sent: {line}")
                return
            except Exception as exc:
                log(f"Serial write failed (attempt {attempt + 1}): {exc}")
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
                        log(f"Serial reconnect failed: {reconnect_exc}")
                        return

    def send_snapshot(self, state: str, mode: str, snapshot: UsageSnapshot | None, display_on: bool) -> None:
        if not display_on:
            self.send_line(f"V1,OFF,{mode},0,--,0,--,--:--,--")
            return
        if snapshot is None:
            values = ("0", "--", "0", "--", "--:--", "--")
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
