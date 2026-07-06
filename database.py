from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    five_hour_percent INTEGER NOT NULL,
    weekly_percent INTEGER NOT NULL,
    five_hour_reset TEXT,
    weekly_reset TEXT,
    api_latency_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_usage_history_timestamp
ON usage_history(timestamp);
"""


def connect_database(path: str) -> sqlite3.Connection:
    db_path = Path(path).expanduser()
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn
