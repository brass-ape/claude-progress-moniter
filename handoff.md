# Claude Usage Monitor — Handoff

## Project goal

A desktop appliance: Raspberry Pi fetches Claude API usage, stores history in SQLite, serves a local web dashboard, and streams data to an Arduino that drives a 16×2 HD44780 LCD. Designed to run for months unattended.

## Design principles

- Reliability over cleverness
- Long uptimes (months)
- Low memory use
- Simple architecture with clear module boundaries
- Easy maintenance

---

## Current architecture

```
claude_lcd.py          Entry point — calls ClaudeMonitorApp().run()
scheduler.py           Central coordinator: fetch loop, state machine, web + serial I/O
client.py              OAuth token load + Anthropic API fetch (with 401 token refresh)
usage.py               Parsing helpers + UsageSnapshot dataclass
history.py             UsageHistory: SQLite record/query (prune, recent, stats, latest_row)
database.py            connect_database(): WAL mode, row_factory, schema migration
serial_display.py      SerialDisplay: background connect loop, heartbeat, send_snapshot()
web.py                 ThreadingHTTPServer: dashboard routes + security headers
logger.py              Levelled log() / warn() / error() + deque(200) ring buffer
static/index.html      Dashboard HTML
static/app.js          Dashboard JS (polling, charts, logs, settings)
static/style.css       Dashboard CSS (dark theme, responsive)
arduino/               Arduino_lcd_display.ino — LCD rendering sketch
docs/api.md            Full HTTP API reference
```

---

## Serial protocol

The Pi sends one packet per second (suppressed if unchanged and within the heartbeat window):

```
V1,<STATE>,<MODE>,<5H_PCT>,<5H_LEFT>,<WEEK_PCT>,<WEEK_LEFT>,<HH:MM>,<DATE>\n
```

Example:
```
V1,OK,AUTO,42,2h13m,18,4d12h,13:42,Mon 6 Jul
```

### STATE values

| Value | Meaning |
|---|---|
| `OK` | Normal — below warning threshold |
| `WARN` | At or above `warning_threshold` (default 80%) |
| `CACHE` | Showing cached data (rate-limited, transient error, or stale) |
| `ERR` | API unreachable, no cached data |
| `OFF` | Display off |

### MODE values

`AUTO`, `FIVE`, `WEEK`, `CLOCK`, `STATUS`

In `AUTO` the Arduino rotates between FIVE → WEEK → CLOCK screens on its own timer. Fixed modes pin the display. The STATUS screen is not included in AUTO rotation; it must be explicitly pinned.

---

## Arduino sketch (`arduino/arduino_lcd_display.ino`)

- HD44780 via `LiquidCrystal`, 4-bit mode (pins 12, 11, 5, 4, 3, 2)
- Custom character slots: 0–5 = progress bar fill levels, 6 = tick glyph (OK), 7 = cross glyph (ERR)
- Status indicator in bottom-right cell (col 15, row 1):
  - Slot 6 (tick) → OK
  - `!` blinking at 500 ms → WARN
  - `*` → CACHE
  - Slot 7 (cross) → ERR
- `drawText()` truncates line 1 to 15 chars to leave room for the indicator
- `updateBlink()` called every loop iteration to drive the WARN blink
- Watchdog timer enabled for reliability
- No dynamic `String` allocation — all fixed buffers

---

## Python modules

### scheduler.py — `ClaudeMonitorApp`

Key methods:

| Method | Description |
|---|---|
| `fetch_once()` | Fetch usage; handles 429 with Retry-After backoff |
| `_seed_from_db()` | On startup: populate state from last DB row so the display is never blank |
| `status()` | Snapshot state under lock, then query DB outside lock |
| `_lcd_state_locked()` | Maps api_status → LCD STATE string |
| `_send_packet()` | Refreshes clock fields on every call before sending to serial |
| `get_settings()` | Return runtime-configurable settings |
| `update_settings(body)` | Validate and apply settings; persist to config.json |

`AppState` is a dataclass holding: `display_on`, `display_mode`, `lcd_state`, `oauth_status`, `internet_status`, `api_status`, `last_error`, `last_success_time`, `last_snapshot`, `retry_after`.

Concurrency: `self.lock` guards all `AppState` reads/writes; `self._fetch_lock` prevents overlapping `fetch_once()` calls.

### history.py — `UsageHistory`

- `record(snapshot)` — insert row
- `latest_row()` — most recent row as dict (used by `_seed_from_db`)
- `recent(hours, max_points=300)` — downsampled chart data using `(rn - 1) % stride = 0` CTE; also always includes the last row
- `stats()` — aggregates (average, peak, trend) + chart data; all SQL-side to avoid pulling large datasets into Python
- `prune(keep_days=7)` — delete old rows; called once per 24 h by the main loop

### client.py — `ClaudeUsageClient`

- Loads OAuth token from `~/.claude/.credentials.json`; caches in `_cached_token`
- On 401/403: force-reloads token and retries once
- Returns `(payload_dict, latency_ms)`

### logger.py

Ring buffer of 200 entries (`collections.deque`), thread-safe. Functions:

- `log(message, level="INFO")`
- `warn(message)` / `error(message)`
- `get_logs(n=200)` → list of `{"ts", "level", "message"}` dicts

All Python modules import from `logger` — print statements are gone.

---

## Web dashboard

Polls `/api/status` every 5 seconds. Key interactive features:

- Usage bars with warn (≥threshold) / danger (≥95%) CSS states
- Mode buttons (AUTO / FIVE / WEEK / CLOCK / STATUS) call POST `/api/display/mode`
- Refresh button calls POST `/api/refresh`
- Power button calls POST `/api/display/on|off`
- **Display settings panel** (collapsible `<details>`) — edit `warning_threshold`, `refresh_seconds`, `stale_after_seconds`; POST to `/api/settings`
- **Logs panel** (collapsible `<details>`) — polls `/api/logs?n=100` every 5 s when open, newest-first, colour-coded by level

Visibility API: polling pauses when the tab is hidden.

---

## Configuration

`config.json` holds only overrides. All defaults are in `DEFAULT_CONFIG` in `scheduler.py`.

Runtime-writable settings (`/api/settings` POST) are validated and merged back into `config.json` atomically.

---

## Known quirks

- **`seven_day` API key** — The Anthropic usage API returns weekly data under the key `seven_day`, not `weekly`. `usage.py` tries `("seven_day", "weekly", "week", "weekly_usage")` in order.
- **Downsampling bug (fixed)** — original query used `rn % stride = 1` which returned zero rows when stride=1. Fixed to `(rn - 1) % stride = 0`.
- **Clock lag (fixed)** — clock was stamped at fetch time and reused for 60 s. Now recomputed in `_send_packet()` on every call using `datetime.now(ZoneInfo(...))`.
- **`status()` lock contention (fixed)** — DB queries now run outside the lock; only the state snapshot is taken under lock.
- **429 → CACHE (not ERR)** — a 429 response means the API is reachable but asking us to wait. `api_status` is set to `rate_limited`, which maps to `CACHE` on the LCD so the last good data remains visible.
- **Startup with no data** — `_seed_from_db()` reads the most recent DB row on startup and pre-populates `last_snapshot` so the display is never blank while waiting for the first fetch.

---

## Test coverage

```bash
python3 -m unittest discover -v
```

- `test_usage.py` — clamp/format/parse helpers, seven_day key, missing buckets
- `test_history.py` — record, latest_row, prune, recent/downsampling, stats/trend
- `test_scheduler.py` — LCD state mapping, get/update settings, _snapshot_from_row, snapshot_to_json

---

## Deployment

Systemd unit at `systemd/claude-monitor.service`. Edit `User=` and `WorkingDirectory=`/`ExecStart=` to match your Pi, then:

```bash
sudo cp systemd/claude-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now claude-monitor
journalctl -u claude-monitor -f
```

After editing `config.json`, a `systemctl restart` is enough (no `daemon-reload` needed unless the unit file changed).

---

## Git workflow

Development is done in Cowork (edit files → git push), then pulled and run on the Pi/laptop:

```bash
# On the Pi / laptop
git pull origin main
sudo systemctl restart claude-monitor
```
