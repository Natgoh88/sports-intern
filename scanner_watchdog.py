"""
scanner_watchdog.py

Dead-man's-switch for the soccer/basketball scanners. Auto-started by
scanner_manager.start() whenever either scanner is started, so it isn't
something you have to remember to turn on separately. Every
CHECK_INTERVAL_SECONDS, checks each scanner that's supposed to be
running for two failure modes a bare "is the process alive" check can't
tell apart:

- dead: the process is gone (crashed, killed, OS reclaimed it)
- hung: the process is still alive but hasn't written a heartbeat
  recently - stuck in a bad state without actually exiting

Either way: send one Telegram alert (deduped with a cooldown so an
extended outage doesn't spam you every 60s) and restart the scanner.
This exists because a scanner dying silently defeats the entire point
of the app - "catch the trigger the moment it fires" is worthless if
nobody notices the catcher stopped catching.

NAMED scanner_watchdog.py, NOT watchdog.py: this project's root
directory is on sys.path, and Streamlit itself depends on the
third-party `watchdog` PyPI package for its file-change auto-reload. A
same-named local watchdog.py here shadows that package for every
process launched from this directory - Streamlit's own `import
watchdog` resolves to this file instead, breaking its ASGI app with
"ImportError: cannot import name 'events' from 'watchdog'" on every
script rerun. Confirmed the hard way during development. Don't rename
this back.
"""

from __future__ import annotations

import asyncio
import time

from dotenv import load_dotenv

import scanner_manager
from alert_dispatcher import TelegramDispatcher, AlertMessage

load_dotenv()

CHECK_INTERVAL_SECONDS = 60

# How long without a heartbeat before a scanner counts as hung. Soccer's
# own idle windows (see run_soccer.py) can legitimately go up to 5
# minutes between iterations, so its threshold needs real margin above
# that. Basketball polls continuously (~20s cycles) and should never
# come close to its threshold if healthy.
STALENESS_THRESHOLD_SECONDS = {
    "soccer": 15 * 60,
    "basketball": 3 * 60,
}

ALERT_COOLDOWN_SECONDS = 30 * 60

_last_alerted: dict[str, float] = {}


async def _alert(name: str, message: str) -> None:
    now = time.time()
    if now - _last_alerted.get(name, 0) < ALERT_COOLDOWN_SECONDS:
        return
    _last_alerted[name] = now
    try:
        dispatcher = TelegramDispatcher()
        await dispatcher.send(
            AlertMessage(
                game_id="watchdog",
                sport="SYSTEM",
                rule_name="scanner_watchdog",
                detail=message,
                market_hint="n/a",
                score_line="n/a",
                game_clock="n/a",
            )
        )
    except Exception as exc:
        print(f"[watchdog] failed to send alert: {exc}")


async def check_once(name: str) -> None:
    if not scanner_manager._pid_file(name).exists():
        return  # never started, or cleanly stopped - nothing to watch

    st = scanner_manager.status(name)
    if not st["running"]:
        print(f"[watchdog] {name} is dead, restarting")
        await _alert(name, f"{name} scanner stopped unexpectedly. Restarting it now.")
        scanner_manager.start(name)
        return

    age = scanner_manager.heartbeat_age_seconds(name)
    threshold = STALENESS_THRESHOLD_SECONDS[name]
    if age is not None and age > threshold:
        print(f"[watchdog] {name} heartbeat stale ({age:.0f}s > {threshold}s), restarting")
        await _alert(name, f"{name} scanner appears hung (no progress in {age / 60:.0f} min). Restarting it now.")
        scanner_manager.stop(name)
        scanner_manager.start(name)


async def run() -> None:
    print("Starting watchdog. Ctrl+C to stop.")
    while True:
        for name in ("soccer", "basketball"):
            try:
                await check_once(name)
            except Exception as exc:
                print(f"[watchdog] check failed for {name}: {exc}")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(run())
