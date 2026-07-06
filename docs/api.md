# Claude Usage Monitor — HTTP API

The web server runs on `http://<device>:8090` by default (configurable via `web_port` in `config.json`).

All endpoints return JSON. Errors are returned as `{"error": "<message>"}` with an appropriate HTTP status code. POST endpoints that accept a body expect `Content-Type: application/json`; body size is capped at 4 KB.

---

## GET /api/status

Returns the current monitor state, live usage numbers, and chart history.

### Response

```json
{
  "display_on": true,
  "display_mode": "AUTO",
  "lcd_state": "OK",
  "oauth_status": "ok",
  "internet_status": "ok",
  "api_status": "ok",
  "last_error": null,
  "last_success": "2026-07-06T12:00:00+00:00",
  "uptime_seconds": 3600,
  "arduino_connected": true,
  "arduino_error": null,
  "rate_limit_seconds": 0,
  "usage": {
    "five_hour_percent": 42,
    "weekly_percent": 18,
    "five_hour_reset": "2026-07-06T17:00:00+00:00",
    "weekly_reset": "2026-07-11T00:00:00+00:00",
    "five_hour_remaining": "4h58m",
    "weekly_remaining": "4d12h",
    "five_hour_reset_label": "17:00",
    "weekly_reset_label": "23:00",
    "clock_time": "12:01:09",
    "clock_date": "Mon 6 Jul",
    "fetched_at": "2026-07-06T12:01:00+00:00",
    "api_latency_ms": 120
  },
  "history": {
    "average_daily_usage": 35.2,
    "peak_utilization": 87,
    "trend": "rising",
    "points_24h": [
      {"timestamp": "2026-07-05T13:00:00+00:00", "five_hour_percent": 22, "weekly_percent": 10, "api_latency_ms": 110},
      "..."
    ],
    "points_7d": ["..."]
  },
  "sysinfo": {
    "line0": "CPU",
    "line1": "42%",
    "cpu_percent": 42,
    "ram_percent": 61,
    "ram_used_gb": 9.8,
    "ram_total_gb": 16.0,
    "gpu_percent": 12,
    "disk_percent": 55,
    "disk_used_gb": 210.4,
    "disk_total_gb": 512.0,
    "disk_io_mbps": 3.2,
    "net_upload_mbps": 0.4,
    "net_download_mbps": 2.1
  }
}
```

#### Field reference

| Field | Type | Description |
|---|---|---|
| `display_on` | bool | Whether the LCD backlight is enabled |
| `display_mode` | string | `AUTO`, `FIVE`, `WEEK`, `CLOCK`, or `STATUS` |
| `lcd_state` | string | `OK`, `WARN`, `CACHE`, `ERR`, or `OFF` |
| `oauth_status` | string | `ok`, `invalid`, or `unknown` |
| `internet_status` | string | `ok`, `offline`, or `unknown` |
| `api_status` | string | `ok`, `using_cache`, `stale`, `rate_limited`, `error`, or `waiting` |
| `last_error` | string\|null | Most recent error message |
| `last_success` | string\|null | ISO-8601 timestamp of last successful fetch |
| `uptime_seconds` | int | Seconds since the service started |
| `arduino_connected` | bool | Whether the serial connection is up |
| `arduino_error` | string\|null | Last serial error message |
| `rate_limit_seconds` | int | Seconds until the next fetch is allowed (0 if not rate-limited) |
| `usage` | object\|null | Current usage snapshot (null before first successful fetch) |
| `history` | object | Historical statistics and chart data |
| `sysinfo` | object | Host CPU/RAM/GPU/Disk/Network readings and the pre-formatted `line0`/`line1` currently shown on the LCD's SYS screen. Any metric field is `null` if unavailable (e.g. `gpu_percent` with no `nvidia-smi`) |

#### `lcd_state` values

| Value | Meaning |
|---|---|
| `OK` | Everything fine, usage below warning threshold |
| `WARN` | Usage at or above `warning_threshold` (default 80%) |
| `CACHE` | Showing cached data (rate-limited, transient error, or stale) |
| `ERR` | API unreachable and no cached data available |
| `OFF` | Display has been turned off |

#### `history.trend` values

Computed over the 7-day window by comparing the average of the first half of rows to the second half.

| Value | Meaning |
|---|---|
| `rising` | Usage trending up (second half > first half by >5%) |
| `falling` | Usage trending down |
| `steady` | No significant change |

#### Chart data (`points_24h` / `points_7d`)

Up to 300 evenly-sampled rows from the respective window. Each point has:

| Field | Type | Description |
|---|---|---|
| `timestamp` | string | ISO-8601 UTC timestamp |
| `five_hour_percent` | int | 5-hour utilisation at that time |
| `weekly_percent` | int | Weekly utilisation at that time |
| `api_latency_ms` | int | API response latency in milliseconds |

---

## POST /api/refresh

Triggers an immediate API fetch (ignored if one is already in progress or the monitor is rate-limited).

Returns the same shape as `GET /api/status`.

---

## POST /api/display/on

Turns the LCD backlight on.

Returns the same shape as `GET /api/status`.

---

## POST /api/display/off

Turns the LCD backlight off. The Arduino receives an `OFF` state packet.

Returns the same shape as `GET /api/status`.

---

## POST /api/display/mode

Sets the LCD display mode.

### Request body (JSON)

```json
{ "mode": "AUTO" }
```

| Field | Values | Description |
|---|---|---|
| `mode` | `AUTO`, `FIVE`, `WEEK`, `CLOCK`, `STATUS`, `SYS` | Screen to show. `AUTO` cycles through screens. Unknown values default to `AUTO`. |

Returns the same shape as `GET /api/status`.

---

## GET /api/logs

Returns recent log entries from the in-memory ring buffer (last 200 entries).

### Query parameters

| Parameter | Default | Description |
|---|---|---|
| `n` | `100` | Number of entries to return (capped at the buffer size of 200) |

### Response

```json
{
  "logs": [
    {"ts": "12:01:00Z", "level": "INFO", "message": "Fetched usage: 5H 42% / week 18% latency 120ms"},
    {"ts": "12:00:58Z", "level": "WARN", "message": "Retrying serial... [Errno 2] No such file or directory"},
    {"ts": "12:00:55Z", "level": "ERROR", "message": "Fetch failed: 429 Too Many Requests"}
  ]
}
```

Entries are ordered oldest-first. Log levels are `INFO`, `WARN`, or `ERROR`.

---

## GET /api/settings

Returns the current runtime-configurable settings.

### Response

```json
{
  "warning_threshold": 80,
  "refresh_seconds": 60,
  "stale_after_seconds": 300,
  "sysinfo_metrics": ["cpu", "ram", "gpu", "disk"],
  "sysinfo_ram_mode": "percent",
  "sysinfo_disk_mode": "percent"
}
```

| Field | Description |
|---|---|
| `warning_threshold` | Percent (1–99) at which bars turn orange and the LCD shows `WARN` |
| `refresh_seconds` | How often (10–3600 s) the monitor polls the Anthropic API |
| `stale_after_seconds` | Seconds (60–86400) after the last successful fetch before data is considered stale |
| `sysinfo_metrics` | Ordered list of enabled system-info metrics shown on the LCD's SYS screen. Values from `cpu`, `ram`, `gpu`, `disk`, `net`. Order and membership are set via the web dashboard's drag-and-drop panel |
| `sysinfo_ram_mode` | `percent` or `used_total` (shows RAM as "used/total GB") |
| `sysinfo_disk_mode` | `percent`, `used_total` (used/total GB), or `io_speed` (combined read+write MB/s) |

---

## POST /api/settings

Updates one or more settings. Changes take effect immediately and are persisted to `config.json`.

### Request body (JSON)

Send only the fields you want to change.

```json
{
  "warning_threshold": 75,
  "refresh_seconds": 120
}
```

Values outside the allowed range are silently ignored. `sysinfo_metrics` entries that aren't one
of `cpu`/`ram`/`gpu`/`disk`/`net` are dropped and duplicates are removed, preserving the order
sent. `sysinfo_ram_mode`/`sysinfo_disk_mode` values outside their allowed sets are ignored.
Returns the updated settings in the same shape as `GET /api/settings`.
