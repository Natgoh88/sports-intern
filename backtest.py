"""
backtest.py

Replays RedCardStateShiftTrigger against real completed EPL/UCL
fixtures - not mock data - to get an honest read on whether the rule's
underlying thesis (the market lags a state change for a few minutes)
holds up, before trusting it live.

WHY ONLY RedCardStateShiftTrigger
------------------------------------
LateCornerCardPressureTrigger (and its z-score sibling,
LateCornerCardPressureZScoreTrigger) need a per-minute shot/corner
timeline to reconstruct historically. api-football's free
/fixtures/events endpoint only returns discrete timestamped events
(goals, cards, substitutions) - not every shot attempt - so there is
no way to reconstruct "shots in the trailing 10 minutes" after the
fact from this data source. RedCardStateShiftTrigger only needs goals
and red cards, both of which ARE discrete timestamped events, so it's
the one rule this data source can actually backtest. This is a real
data-granularity limit, not something worth faking around - see
README's Data sources section for the same caveat noted live.

METHODOLOGY
------------------------------------
Given a fixture's real events list and real pre-match odds (the same
/odds endpoint used live), reconstruct_minute_states() replays the
match minute-by-minute exactly the way the live scanner would have
seen it developing. backtest_fixture() then runs the actual production
RedCardStateShiftTrigger.evaluate() - unchanged, not a reimplementation
- against each minute snapshot.

"Would it have won" is judged against the fixture's real final score on
the suggested market (opponent moneyline/draw-or-better) - a real
result, not a fabricated one. This does NOT compute CLV: that needs a
real closing-line snapshot at multiple points in time, which doesn't
exist after the fact for a past match on the free tier (only one odds
snapshot is available per fixture, not a time series). It reports raw
hit rate instead, which is a real, if cruder, signal - and is honest
about that being a simplification rather than pretending to have CLV
data that doesn't exist.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass

from soccer_scanner import RedCardStateShiftTrigger, SoccerGameState, TeamMatchState
from clv_engine import power_devig


@dataclass
class BacktestFire:
    minute: int
    favorite_team: str
    reason: str
    suggested_side: str  # the opponent - what the trigger recommends looking at
    would_have_won: bool  # suggested_side did not lose (won or drew, since market_hint includes handicap/total angles too)


def _pre_match_win_probs_from_odds(odds_json: dict, home_name: str, away_name: str) -> dict[str, float]:
    for entry in odds_json.get("response", []):
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


def reconstruct_minute_states(
    fixture_json: dict,
    events_json: list[dict],
    pre_match_win_probs: dict[str, float],
) -> list[SoccerGameState]:
    """Builds one SoccerGameState per minute from 0 to full time,
    replaying real events (goals, red cards) chronologically - this is
    what the live scanner would have seen at each point, reconstructed
    after the fact instead of polled live."""
    fixture = fixture_json["fixture"]
    teams = fixture_json["teams"]
    home_id, away_id = teams["home"]["id"], teams["away"]["id"]
    home_name, away_name = teams["home"]["name"], teams["away"]["name"]
    full_time_minute = (fixture_json["fixture"]["status"].get("elapsed") or 90) + 5  # small buffer for stoppage

    home = TeamMatchState(team_id=str(home_id), name=home_name, pre_match_win_prob=pre_match_win_probs.get(home_name, 0.5))
    away = TeamMatchState(team_id=str(away_id), name=away_name, pre_match_win_prob=pre_match_win_probs.get(away_name, 0.5))

    # sort real events chronologically so state accumulates correctly
    timed_events = sorted(events_json, key=lambda e: e.get("time", {}).get("elapsed", 0))

    states = []
    event_idx = 0
    for minute in range(0, full_time_minute + 1):
        while event_idx < len(timed_events) and timed_events[event_idx].get("time", {}).get("elapsed", 0) <= minute:
            event = timed_events[event_idx]
            team_id = event.get("team", {}).get("id")
            if event.get("type") == "Goal":
                if team_id == home_id:
                    home.shots += 0  # not tracked here, only score matters for this trigger
                # score bump handled via states[-1] copy below
            if event.get("type") == "Card" and event.get("detail") == "Red Card":
                if team_id == home_id:
                    home.red_cards += 1
                elif team_id == away_id:
                    away.red_cards += 1
            event_idx += 1

        # recompute cumulative score up to this minute from goal events directly (simpler than mutating incrementally above)
        home_score = sum(1 for e in timed_events if e.get("type") == "Goal" and e.get("team", {}).get("id") == home_id and e.get("time", {}).get("elapsed", 0) <= minute)
        away_score = sum(1 for e in timed_events if e.get("type") == "Goal" and e.get("team", {}).get("id") == away_id and e.get("time", {}).get("elapsed", 0) <= minute)

        states.append(
            SoccerGameState(
                game_id=str(fixture["id"]),
                home=TeamMatchState(team_id=home.team_id, name=home.name, pre_match_win_prob=home.pre_match_win_prob, red_cards=home.red_cards),
                away=TeamMatchState(team_id=away.team_id, name=away.name, pre_match_win_prob=away.pre_match_win_prob, red_cards=away.red_cards),
                minute=minute,
                home_score=home_score,
                away_score=away_score,
            )
        )
    return states


def backtest_fixture(
    fixture_json: dict,
    events_json: list[dict],
    odds_json: dict,
    trigger: RedCardStateShiftTrigger | None = None,
) -> list[BacktestFire]:
    """Runs the real, unmodified RedCardStateShiftTrigger against a
    reconstructed replay of one completed fixture. Returns every
    hypothetical fire with whether the suggested side ended up winning
    or drawing (a losing bet on the suggested side is a miss)."""
    trigger = trigger or RedCardStateShiftTrigger()
    teams = fixture_json["teams"]
    home_name, away_name = teams["home"]["name"], teams["away"]["name"]
    probs = _pre_match_win_probs_from_odds(odds_json, home_name, away_name)

    states = reconstruct_minute_states(fixture_json, events_json, probs)
    final_home_score = fixture_json["goals"].get("home") or 0
    final_away_score = fixture_json["goals"].get("away") or 0

    fires = []
    for state in states:
        event = trigger.evaluate(state)
        if event is None:
            continue
        favorite_id = event.metadata["team"]
        is_home_favorite = favorite_id == state.home.team_id
        suggested_side = state.away.name if is_home_favorite else state.home.name
        # "won" for the suggested side = didn't lose the match outright
        # (moneyline/handicap value on the opponent covers draw-or-better,
        # not just an outright win - see the trigger's own market_hint)
        if is_home_favorite:
            would_have_won = final_away_score >= final_home_score
        else:
            would_have_won = final_home_score >= final_away_score

        fires.append(
            BacktestFire(
                minute=state.minute,
                favorite_team=state.home.name if is_home_favorite else state.away.name,
                reason=event.message,
                suggested_side=suggested_side,
                would_have_won=would_have_won,
            )
        )
    return fires


async def backtest_fixture_id(fixture_id: str, api_key: str) -> list[BacktestFire]:
    """Fetches a completed fixture's real data and backtests it - the
    live equivalent of backtest_fixture() for ad-hoc use against a
    specific match. Costs 3 real API-Football requests (fixture,
    events, odds)."""
    import aiohttp
    from api_football_adapter import BASE_URL

    async with aiohttp.ClientSession(headers={"x-apisports-key": api_key}) as session:
        async with session.get(f"{BASE_URL}/fixtures", params={"id": fixture_id}) as resp:
            fixture_data = await resp.json()
        async with session.get(f"{BASE_URL}/fixtures/events", params={"fixture": fixture_id}) as resp:
            events_data = await resp.json()
        async with session.get(f"{BASE_URL}/odds", params={"fixture": fixture_id}) as resp:
            odds_data = await resp.json()

    fixture_json = fixture_data["response"][0]
    events_json = events_data.get("response", [])
    return backtest_fixture(fixture_json, events_json, odds_data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("fixture_id", help="a completed EPL/UCL fixture id to backtest")
    parser.add_argument("--key", required=True, help="your api-football key")
    args = parser.parse_args()

    fires = asyncio.run(backtest_fixture_id(args.fixture_id, args.key))
    if not fires:
        print("No RedCardStateShiftTrigger conditions occurred in this match.")
    for f in fires:
        outcome = "WOULD HAVE WON" if f.would_have_won else "would have lost"
        print(f"minute {f.minute}: {f.reason}")
        print(f"  -> suggested side: {f.suggested_side} | {outcome}")
