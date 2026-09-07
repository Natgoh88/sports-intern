"""
run_basketball.py

Wires ESPNBasketballAdapter -> TriggerEngine -> AlertRouter(Telegram) -> alert_log.
Started here so it can run as its own process/service independent of soccer.
Reads poll interval, trigger thresholds, and key_players from config.json
(see config_store.py) - edit those from the dashboard's Settings tab
instead of this file.

NOTE: espn_basketball_adapter.py's JSON parsing has NOT been verified against
a live game yet (NBA off-season until 2026-10-03 preseason / NCAAM until
2026-11-02 - see README). Run `python espn_basketball_adapter.py --debug
<event_id>` once a real game is live and fix any field paths before trusting
alerts from this script.
"""

from __future__ import annotations

import asyncio

from dotenv import load_dotenv

from basketball_scanner import TriggerEngine, BonusTrigger, FoulTroubleTrigger, League
from alert_dispatcher import AlertRouter, TelegramDispatcher, AlertMessage
from alert_log import log_trigger
from espn_basketball_adapter import ESPNBasketballAdapter
from config_store import load_config
import scanner_manager

load_dotenv()


async def on_trigger(event):
    log_trigger(event)
    await router.dispatch(
        AlertMessage(
            game_id=event.game_id,
            sport="NBA",
            rule_name=event.rule_name,
            detail=event.message,
            market_hint=event.market_hint,
            score_line="",
            game_clock="",
        )
    )


cfg = load_config()["basketball"]

router = AlertRouter(channels=[TelegramDispatcher()])

engine = TriggerEngine(
    adapter=ESPNBasketballAdapter(league=League.NBA, key_players=cfg["key_players"]),
    rules=[
        BonusTrigger(min_seconds_remaining=cfg["bonus_trigger"]["min_seconds_remaining"]),
        FoulTroubleTrigger(
            foul_count_threshold=cfg["foul_trouble_trigger"]["foul_count_threshold"],
            early_period_cutoff=cfg["foul_trouble_trigger"]["early_period_cutoff"],
        ),
    ],
    on_trigger=on_trigger,
    poll_interval_seconds=cfg["poll_interval_seconds"],
)

async def _heartbeat_loop():
    """TriggerEngine.run() has no per-tick hook to write from, so this
    runs alongside it instead - independent of whether a tick found any
    games or fired any triggers, which is what the watchdog actually
    needs to tell 'healthy but quiet' apart from 'hung'."""
    while True:
        scanner_manager.write_heartbeat("basketball")
        await asyncio.sleep(15)


async def _main():
    await asyncio.gather(engine.run(), _heartbeat_loop())


if __name__ == "__main__":
    print("Starting basketball scanner (NBA). Ctrl+C to stop.")
    asyncio.run(_main())
