"""
run_soccer.py

Wires APIFootballAdapter -> (EPL/UCL filter) -> soccer_scanner rules ->
AlertRouter(Telegram) -> alert_log. soccer_scanner.py only defines the
TriggerRules, not a polling loop (unlike basketball_scanner.TriggerEngine),
so the loop lives here.

api_football_adapter.APIFootballAdapter.active_games() returns every live
fixture worldwide (confirmed: 7 live fixtures during initial testing on
2026-09-07, none of them EPL/UCL). Polling all of those would blow through
the 100 req/day free tier fast, so this script filters the live-fixtures
list down to EPL (league 39) and UCL (league 2) before calling
adapter.poll() on each one.

pre_match_win_probs is required by RedCardStateShiftTrigger /
LateCornerCardPressureTrigger (favorite-vs-underdog logic). api-football's
free tier doesn't hand you de-vigged probabilities directly, but it does
expose a per-fixture /odds endpoint (same key, confirmed working on the
free tier against real scheduled fixtures) with a "Match Winner" market -
fetch_pre_match_win_probs() below pulls that market's Home/Draw/Away
prices and runs them through clv_engine.power_devig() the moment a game
is first seen, so both triggers get real favorite/underdog data instead
of the 0.5/0.5 default. If no bookmaker has posted odds yet for a fixture
(happens right up until shortly before kickoff), it falls back to 0.5/0.5
for that game until odds appear.
"""

from __future__ import annotations

import asyncio
import os

import aiohttp
from dotenv import load_dotenv

from soccer_scanner import RedCardStateShiftTrigger, LateCornerCardPressureTrigger
from api_football_adapter import APIFootballAdapter, BASE_URL
from alert_dispatcher import AlertRouter, TelegramDispatcher, AlertMessage
from alert_log import log_trigger
from clv_engine import power_devig
from config_store import load_config

load_dotenv()

TARGET_LEAGUE_IDS = {39, 2}  # EPL, UCL


async def fetch_pre_match_win_probs(session: aiohttp.ClientSession, fixture_id: str) -> dict[str, float]:
    """Pulls the "Match Winner" (1X2) market for a fixture from every
    bookmaker api-football has odds for, and de-vigs the first complete
    one it finds. Returns {} if no bookmaker has posted odds yet."""
    async with session.get(f"{BASE_URL}/fixtures", params={"id": fixture_id}) as resp:
        resp.raise_for_status()
        fixture_data = await resp.json()
    if not fixture_data.get("response"):
        return {}
    teams = fixture_data["response"][0]["teams"]
    home_name, away_name = teams["home"]["name"], teams["away"]["name"]

    async with session.get(f"{BASE_URL}/odds", params={"fixture": fixture_id}) as resp:
        resp.raise_for_status()
        odds_data = await resp.json()

    for entry in odds_data.get("response", []):
        for bookmaker in entry.get("bookmakers", []):
            for bet in bookmaker.get("bets", []):
                if bet.get("name") != "Match Winner":
                    continue
                prices = {v["value"]: float(v["odd"]) for v in bet.get("values", [])}
                if not all(k in prices for k in ("Home", "Draw", "Away")):
                    continue
                fair = power_devig([prices["Home"], prices["Draw"], prices["Away"]])
                return {home_name: fair[0], away_name: fair[2]}
    return {}


class FilteredAPIFootballAdapter(APIFootballAdapter):
    """Same as APIFootballAdapter, but active_games() only returns fixtures
    in TARGET_LEAGUE_IDS instead of every live match worldwide."""

    async def active_games(self) -> list[str]:
        async with aiohttp.ClientSession(headers=self._headers()) as session:
            async with session.get(f"{BASE_URL}/fixtures", params={"live": "all"}) as resp:
                resp.raise_for_status()
                data = await resp.json()
        return [
            str(f["fixture"]["id"])
            for f in data.get("response", [])
            if f.get("league", {}).get("id") in TARGET_LEAGUE_IDS
        ]


async def on_trigger(event):
    log_trigger(event)
    await router.dispatch(
        AlertMessage(
            game_id=event.game_id,
            sport="EPL/UCL",
            rule_name=event.rule_name,
            detail=event.message,
            market_hint=event.market_hint,
            score_line="",
            game_clock="",
        )
    )


cfg = load_config()["soccer"]

router = AlertRouter(channels=[TelegramDispatcher()])
rules = [
    RedCardStateShiftTrigger(
        favorite_prob_threshold=cfg["red_card_state_shift"]["favorite_prob_threshold"],
        minute_cutoff=cfg["red_card_state_shift"]["minute_cutoff"],
    ),
    LateCornerCardPressureTrigger(
        minute_start=cfg["late_pressure_cooker"]["minute_start"],
        shot_spike_threshold=cfg["late_pressure_cooker"]["shot_spike_threshold"],
        corner_spike_threshold=cfg["late_pressure_cooker"]["corner_spike_threshold"],
    ),
]


async def run(poll_interval_seconds: int | None = None):
    poll_interval_seconds = poll_interval_seconds or cfg["poll_interval_seconds"]
    api_key = os.environ.get("API_FOOTBALL_KEY")
    if not api_key:
        raise SystemExit("API_FOOTBALL_KEY not set - add it from the dashboard's Settings tab")

    adapter = FilteredAPIFootballAdapter(api_key=api_key, pre_match_win_probs={})
    odds_fetched_for: set[str] = set()

    while True:
        game_ids = await adapter.active_games()
        async with aiohttp.ClientSession(headers=adapter._headers()) as session:
            for game_id in game_ids:
                if game_id not in odds_fetched_for:
                    probs = await fetch_pre_match_win_probs(session, game_id)
                    adapter.pre_match_win_probs.update(probs)
                    odds_fetched_for.add(game_id)

        for game_id in game_ids:
            state = await adapter.poll(game_id)
            for rule in rules:
                event = rule.evaluate(state)
                if event:
                    await on_trigger(event)
        await asyncio.sleep(poll_interval_seconds)


if __name__ == "__main__":
    print("Starting soccer scanner (EPL/UCL). Ctrl+C to stop.")
    asyncio.run(run())
