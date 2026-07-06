# Serial Protocol

The Pi sends one newline-terminated ASCII packet to the Arduino. The current protocol is versioned as `V1`.

```text
V1,STATE,MODE,FIVE_PERCENT,FIVE_LEFT,WEEK_PERCENT,WEEK_LEFT,TIME,DATE
```

Example:

```text
V1,OK,AUTO,42,2h13m,18,4d12h,13:42,Mon 6 Jul
```

## State

- `OK`: fresh API data
- `WARN`: fresh API data, high 5-hour usage
- `CACHE`: using stale or cached data
- `ERR`: API/network/OAuth error before usable data
- `OFF`: LCD display should sleep

## Mode

- `AUTO`: Arduino rotates between 5-hour, week, and clock; status takes priority on errors/cache
- `FIVE`: pin 5-hour usage screen
- `WEEK`: pin weekly usage screen
- `CLOCK`: pin clock screen
- `STATUS`: pin status screen

The Pi resends heartbeat packets so the Arduino resynchronizes after a reboot.
