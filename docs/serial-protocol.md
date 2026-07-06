# Serial Protocol

The Pi sends one newline-terminated ASCII packet to the Arduino. The current protocol is versioned as `V1`.

```text
V1,STATE,MODE,FIVE_PERCENT,FIVE_LEFT,WEEK_PERCENT,WEEK_LEFT,TIME,DATE
```

Example:

```text
V1,OK,AUTO,42,2h13m,18,4d12h,13:42:09,Mon 6 Jul
```

A second packet type carries the system-info (CPU/RAM/GPU/Disk/Network) screen content:

```text
S1,LINE0,LINE1
```

Example:

```text
S1,CPU,42%
S1,Disk I/O,12.4MB/s
S1,Net MB/s,U1.2 D5.6
```

`LINE0`/`LINE1` are already fully formatted by the Pi (units, GB/MB-per-second math, which
metric is currently due) — the Arduino just prints them, the same way it already prints the
Pi-formatted `clock_time`/`clock_date` strings for the `CLOCK` screen. `LINE1` stays within the
15-character budget so column 15 remains free for the status indicator glyph.

## State

- `OK`: fresh API data
- `WARN`: fresh API data, high 5-hour usage
- `CACHE`: using stale or cached data
- `ERR`: API/network/OAuth error before usable data
- `OFF`: LCD display should sleep

## Mode

- `AUTO`: Arduino rotates between 5-hour, week, clock, and system-info; status takes priority on errors/cache
- `FIVE`: pin 5-hour usage screen
- `WEEK`: pin weekly usage screen
- `CLOCK`: pin clock screen
- `STATUS`: pin status screen
- `SYS`: pin the system-info screen (CPU/RAM/GPU/Disk/Network, whichever the Pi is currently
  showing per the web dashboard's enabled/ordered metric list)

The Pi resends heartbeat packets so the Arduino resynchronizes after a reboot.
