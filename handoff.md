# Claude Usage Monitor Handoff

## Project Goal

Build a polished, open-source hardware monitor for Claude usage using:

- Raspberry Pi as the main controller
- Arduino Uno as the LCD controller
- 16x2 HD44780 LCD

The device should behave like a small desktop appliance that continuously displays Claude usage without needing a browser open.

## Design Priorities

- Reliability over cleverness
- Long uptimes measured in months
- Low memory use
- Simple architecture
- Clear module boundaries
- Easy maintenance
- Open-source friendliness

## Current Workspace

The project currently has a small starting implementation:

- `claude_lcd.py`: Python script that fetches Claude OAuth usage, serves a tiny web control page, and sends serial updates to the Arduino.
- `arduino_display.ino`: Arduino sketch that receives compact serial lines and renders usage on a 16x2 LCD.
- `refresh.log`: existing log file.

There is no git repository initialized in this workspace.

## Existing Python Behavior

`claude_lcd.py` currently:

- Reads credentials from `~/.claude/.credentials.json`
- Calls `https://api.anthropic.com/api/oauth/usage`
- Parses 5-hour usage only
- Converts reset time to `Europe/London`
- Opens serial on `/dev/ttyACM0` at `115200`
- Serves a small LAN-only web page on port `8090`
- Supports display on/off via HTTP
- Sends old serial protocol lines like:
  - `OK,37,16:59`
  - `WARN,83,16:59`
  - `STALE,37,16:59`
  - `OFF,0,--:--`

## Existing Arduino Behavior

`arduino_display.ino` currently:

- Uses `LiquidCrystal`
- Uses fixed-size buffers and avoids Arduino `String`
- Enables the watchdog timer
- Parses the old `STATE,PERCENT,RESET` protocol
- Draws 5-hour usage with a smooth custom-character progress bar
- Updates only changed LCD portions
- Blinks a warning indicator for high usage
- Turns the LCD display off for `OFF`

Keep this robustness philosophy intact.

## Desired Project Structure

The target structure from the project brief is:

```text
claude-monitor/
├── config.json
├── client.py
├── scheduler.py
├── usage.py
├── serial_display.py
├── history.py
├── web.py
├── logger.py
├── database.py
├── static/
│   ├── index.html
│   ├── style.css
│   └── app.js
├── arduino/
│   └── arduino_lcd_display.ino
├── docs/
└── README.md
```

Each module should have one clear responsibility.

## Important Updated Requirement

The LCD should not only auto-cycle through screens. Screen behavior must be configurable from the web UI.

Recommended approach:

- Add a dashboard control for display mode:
  - `Auto`
  - `5-hour`
  - `Week`
  - `Clock`
  - `Status`
- Store this mode in Pi-side shared app state.
- Include the selected display mode in the versioned serial protocol.
- Let the Arduino remain simple:
  - `Auto` means Arduino rotates screens locally every few seconds.
  - Fixed modes pin the LCD to that screen.
- Keep display on/off separate from selected mode.

## LCD Screens

### 5-hour Screen

```text
100% ████████ !
00:00 LEFT  5H
```

Shows 5-hour utilization, smooth progress bar, and remaining time until reset.

### Weekly Screen

```text
Week 18%
4d12h left
```

Shows weekly utilization and remaining time until weekly reset.

### Clock Screen

```text
13:42

Mon 6 Jul
```

Acts as a small desk clock when idle. The Pi should send the current local time/date so the Arduino does not need timezone logic.

### Status Screen

Examples:

```text
API Offline
```

```text
Network Error
```

```text
OAuth Invalid
```

```text
Using Cache
```

This screen can appear automatically in `Auto` mode when required, and can also be pinned from the web UI.

## Serial Protocol Direction

Move to a versioned compact protocol. A practical format is:

```text
V1,STATE,MODE,FIVE_PERCENT,FIVE_LEFT,WEEK_PERCENT,WEEK_LEFT,TIME,DATE
```

Example:

```text
V1,OK,AUTO,42,2h13m,18,4d12h,13:42,Mon 6 Jul
```

Suggested `STATE` values:

- `OK`
- `WARN`
- `CACHE`
- `ERR`
- `OFF`

Suggested `MODE` values:

- `AUTO`
- `FIVE`
- `WEEK`
- `CLOCK`
- `STATUS`

Heartbeat packets should continue so an Arduino reboot resynchronizes automatically.

## Web Dashboard Requirements

Replace the minimal control page with a polished vanilla HTML/CSS/JS dashboard.

Display:

- Current 5-hour usage
- Weekly usage
- Remaining time
- Last successful API refresh
- API latency
- Pi uptime
- Arduino connection status
- OAuth status
- Internet status
- LCD state
- Display on/off controls
- LCD screen mode control
- Manual refresh button

The page should auto-refresh using JavaScript without reloading the browser.

## Historical Data

Use SQLite and store every successful fetch.

Suggested schema:

- `timestamp`
- `five_hour_percent`
- `weekly_percent`
- `five_hour_reset`
- `weekly_reset`
- `api_latency_ms`

History should support:

- 24-hour graph
- 7-day graph
- Average daily usage
- Peak utilization
- Usage trends

## Implementation Notes For Next Conversation

The previous attempt to apply a large patch was interrupted and did not complete. It may have created directories depending on where it stopped, but no reliable refactor should be assumed without inspecting the workspace.

Start by checking:

```bash
find . -maxdepth 2 -type f -print
sed -n '1,240p' claude_lcd.py
sed -n '1,260p' arduino_display.ino
```

Then proceed in small commits or patches:

1. Add `config.json`, module skeletons, and `static/` dashboard files.
2. Keep `claude_lcd.py` as a compatibility entrypoint that calls `scheduler.main()`.
3. Add SQLite history and usage parsing.
4. Implement dashboard API endpoints for status, manual refresh, display on/off, and display mode.
5. Update serial protocol to include display mode, weekly usage, remaining times, local clock text, and status.
6. Add `arduino/arduino_lcd_display.ino` with versioned parsing and screen mode handling.
7. Optionally leave the old `arduino_display.ino` as legacy or replace it with a comment pointing to the new sketch.
8. Run syntax checks for Python and, if available, compile/verify the Arduino sketch.

## Environment Note

During the prior conversation, normal sandboxed commands failed with:

```text
bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted
```

Read-only commands had to be rerun with escalated permissions. If this continues, use escalated commands when needed and explain why.
