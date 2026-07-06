# Claude Usage Monitor

A Raspberry Pi + Arduino appliance that shows your Claude API usage on a 16×2 HD44780 LCD and serves a local web dashboard.

## Hardware

- **Raspberry Pi** — fetches usage from the Anthropic API, stores history in SQLite, serves the web dashboard, and streams data to the Arduino over USB serial
- **Arduino Uno** — drives the 16×2 HD44780 LCD in 4-bit mode
- **16×2 HD44780 LCD** — shows 5-hour and weekly utilisation, a live clock, and a status indicator glyph in the bottom-right corner

## Setup

### 1. Install Python dependencies

```bash
python3 -m pip install -r requirements.txt
```

### 2. Configure (optional)

`config.json` in the project root holds overrides. Values you can change:

| Key | Default | Description |
|---|---|---|
| `timezone` | `"Europe/London"` | IANA timezone for the clock and reset labels |
| `serial_port` | `"/dev/ttyACM0"` | Serial port the Arduino is on |
| `web_port` | `8090` | Port the dashboard listens on |
| `refresh_seconds` | `60` | How often the API is polled |
| `warning_threshold` | `80` | % at which bars/LCD turn amber |
| `prune_days` | `7` | How many days of history to keep |

Only include the keys you want to override — everything else uses the default.

### 3. Upload the Arduino sketch

Open `arduino/arduino_lcd_display.ino` in the Arduino IDE and upload it to the Uno. The sketch expects data over serial at 115200 baud.

### 4. Start the monitor

```bash
python3 claude_lcd.py
```

The dashboard is at `http://<pi-hostname>:8090/` — or `http://raspberrypi.local:8090/` if Avahi/mDNS is enabled.

## LCD display

The LCD rotates through three screens in AUTO mode: 5-hour, Week, and Clock. A status indicator glyph appears in the bottom-right corner at all times:

| Glyph | Meaning |
|---|---|
| ✓ (tick) | OK — below warning threshold |
| `!` (blinking) | WARN — at or above warning threshold |
| `*` | CACHE — showing cached data (rate-limited or transient error) |
| ✗ (cross) | ERR — API unreachable, no cached data |

Use the **Status** button in the dashboard (or `MODE=STATUS`) to pin the full status screen on the LCD.

## Web dashboard

Open `http://<pi>:8090/` from any device on the same network.

### Features

- **Usage meters** — 5-hour and weekly bars with warn/danger colour states (amber ≥ threshold, red ≥ 95%)
- **Status grid** — last refresh time, API latency, Pi uptime, Arduino connection, OAuth/network status, LCD state, and usage trend
- **Charts** — 24-hour and 7-day history charts (sampled to ≤ 300 points)
- **LCD controls** — set the display mode (Auto / 5-hour / Week / Clock / Status) or turn the backlight off
- **Manual refresh** — force an immediate API fetch
- **Display settings** — adjust `warning_threshold`, `refresh_seconds`, and `stale_after_seconds` at runtime; settings are persisted to `config.json`
- **Logs panel** — collapsible live log viewer (last 100 entries, updates every 5 s when open)
- **PWA-ready** — add to iPhone Home Screen via Safari Share → "Add to Home Screen" for a full-screen icon

### Controlling from a phone

Because the dashboard binds to `0.0.0.0`, any phone on the same Wi-Fi can reach it. For one-tap actions, use the iOS Shortcuts app with a "Get Contents of URL" action:

| Action | Method | URL |
|---|---|---|
| Manual refresh | POST | `http://raspberrypi.local:8090/api/refresh` |
| LCD on | POST | `http://raspberrypi.local:8090/api/display/on` |
| LCD off | POST | `http://raspberrypi.local:8090/api/display/off` |
| Set mode | POST + body `{"mode":"CLOCK"}` | `http://raspberrypi.local:8090/api/display/mode` |

The endpoints have no authentication — fine on a trusted home LAN, but don't port-forward to the internet without adding auth.

See [docs/api.md](docs/api.md) for the full API reference.

## Running as a service

A systemd unit is included. Edit the `User=` and `WorkingDirectory=` / `ExecStart=` paths in `systemd/claude-monitor.service` to match your Pi, then:

```bash
sudo cp systemd/claude-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now claude-monitor
```

Tail logs live:

```bash
journalctl -u claude-monitor -f
```

After editing `config.json`, restart with:

```bash
sudo systemctl restart claude-monitor
```

## Tests

```bash
python3 -m unittest discover -v
```

This runs:

- `test_usage.py` — parsing and formatting helpers in `usage.py`
- `test_history.py` — SQLite history: record, latest_row, prune, recent/downsampling, stats
- `test_scheduler.py` — LCD state logic, settings get/update, snapshot serialisation

## Architecture overview

```
claude_lcd.py (entry point)
└── ClaudeMonitorApp (scheduler.py)
    ├── ClaudeUsageClient (client.py)      OAuth token + API fetch
    ├── UsageHistory (history.py)          SQLite via database.py
    ├── SerialDisplay (serial_display.py)  USB serial to Arduino
    ├── run_server (web.py)                ThreadingHTTPServer dashboard
    └── logger.py                          Levelled log buffer (deque[200])
```

Data flow: `fetch_once()` → `parse_usage_payload()` → `history.record()` → `display.send_snapshot()` + web `/api/status`.

The serial packet format sent to the Arduino is:

```
V1,<STATE>,<MODE>,<5H_PCT>,<5H_LEFT>,<WEEK_PCT>,<WEEK_LEFT>,<HH:MM:SS>,<DATE>\n
```

`STATE` is one of `OK`, `WARN`, `CACHE`, `ERR`, or `OFF`. `MODE` is the current display mode.
