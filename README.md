# Claude Usage Monitor

A small Raspberry Pi and Arduino appliance for displaying Claude usage on a 16x2 HD44780 LCD.

## Hardware

- Raspberry Pi for OAuth credentials, API calls, history, dashboard, and serial output
- Arduino Uno for LCD rendering only
- 16x2 HD44780 LCD in 4-bit mode

## Run

Install the Python dependencies:

```bash
python3 -m pip install -r requirements.txt
```

Check `config.json`, then start the monitor:

```bash
python3 claude_lcd.py
```

Open the dashboard on the Pi at:

```text
http://<pi-hostname-or-ip>:8090/
```

## Dashboard

The dashboard shows current usage, API freshness, latency, Pi uptime, Arduino connection status, OAuth/network state, historical charts, display power, manual refresh, and LCD screen mode.

LCD mode can be set to:

- Auto
- 5-hour
- Week
- Clock
- Status

## Controlling from a phone (LAN)

The dashboard binds to `0.0.0.0:8090`, so any phone on the same Wi-Fi can reach
it — no extra server setup needed.

- **Stable address**: Raspberry Pi OS runs Avahi by default, so
  `http://raspberrypi.local:8090` works without hunting for an IP. If you've
  renamed the Pi, substitute your hostname, or set a static DHCP reservation
  on your router so the IP never changes.
- **App-like icon**: open the dashboard in Safari, tap Share → "Add to Home
  Screen" for a full-screen icon with no browser chrome.
- **One-tap actions via Shortcuts**: create a Shortcut with a "Get Contents of
  URL" action (method `POST`) pointed at one of these, then add it to your
  Home Screen or trigger it by Siri phrase:
  - `http://raspberrypi.local:8090/api/refresh` — manual refresh
  - `http://raspberrypi.local:8090/api/display/on` / `/api/display/off` — LCD power
  - `http://raspberrypi.local:8090/api/display/mode` with JSON body `{"mode": "CLOCK"}` — pin a screen

There's no authentication on these endpoints — fine on a trusted home LAN, but
don't port-forward 8090 to the internet without adding auth first.

## Tests

Pure parsing/formatting logic in `usage.py` has unit test coverage:

```bash
python3 -m unittest test_usage.py -v
```

## Running as a service

For an appliance-style deployment that survives reboots and crashes, install the
provided systemd unit. It's pre-filled for this workspace (`User=yoxie`,
`/home/yoxie/claude_progress`) — edit those three lines first if you deploy
elsewhere:

```bash
sudo cp systemd/claude-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now claude-monitor
```

`config.json` and `usage_history.sqlite3` are resolved relative to the project
directory (not the current working directory), so the service starts up the
same way whether it's launched by systemd, cron, or a terminal.

If you edit `systemd/claude-monitor.service` later, re-copy it and reload:

```bash
sudo cp systemd/claude-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart claude-monitor
sudo systemctl status claude-monitor   # confirm it came back up
journalctl -u claude-monitor -f        # tail logs live
```

`daemon-reload` is only needed when the unit *file* changes (new
`ExecStart`, `Restart` policy, etc.) — editing `config.json` just needs a
`restart`.

## Arduino

Upload `arduino/arduino_lcd_display.ino` to the Uno. The older root-level `arduino_display.ino` is the original sketch and is kept for reference while the project is being refactored.
