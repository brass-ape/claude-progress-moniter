from __future__ import annotations

import collections
import threading
from datetime import datetime, timezone
from typing import Literal

Level = Literal["INFO", "WARN", "ERROR"]

_BUFFER_SIZE = 200
_buffer: collections.deque[dict] = collections.deque(maxlen=_BUFFER_SIZE)
_lock = threading.Lock()


def log(message: str, level: Level = "INFO") -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    entry = {"ts": f"{ts}Z", "level": level, "message": message}
    with _lock:
        _buffer.append(entry)
    print(f"{ts}Z [{level}] {message}", flush=True)


def warn(message: str) -> None:
    log(message, level="WARN")


def error(message: str) -> None:
    log(message, level="ERROR")


def get_logs(n: int = _BUFFER_SIZE) -> list[dict]:
    """Return the most recent n log entries, newest last."""
    with _lock:
        entries = list(_buffer)
    return entries[-n:]
