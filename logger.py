from __future__ import annotations

import collections
import threading
from datetime import datetime, timezone
from typing import Literal

Level = Literal["INFO", "WARN", "ERROR"]

_BUFFER_SIZE = 200
_buffer: collections.deque[dict] = collections.deque(maxlen=_BUFFER_SIZE)
_lock = threading.Lock()

# Fixed, known set of log sources (one per subsystem) so the web UI can offer a
# stable filter list even before an entry from every source has been produced.
SOURCES = ("system", "usage", "serial", "web", "db", "settings")


def log(message: str, level: Level = "INFO", source: str = "system") -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    entry = {"ts": f"{ts}Z", "level": level, "source": source, "message": message}
    with _lock:
        _buffer.append(entry)
    print(f"{ts}Z [{level}] [{source}] {message}", flush=True)


def warn(message: str, source: str = "system") -> None:
    log(message, level="WARN", source=source)


def error(message: str, source: str = "system") -> None:
    log(message, level="ERROR", source=source)


def get_logs(n: int = _BUFFER_SIZE, source: str | None = None, level: str | None = None) -> list[dict]:
    """Return the most recent n log entries (optionally filtered by source/level), newest last."""
    with _lock:
        entries = list(_buffer)
    if source:
        entries = [e for e in entries if e["source"] == source]
    if level:
        level = level.upper()
        entries = [e for e in entries if e["level"] == level]
    return entries[-n:]
