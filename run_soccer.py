"""
run_soccer.py

Wires APIFootballAdapter -> soccer_scanner rules -> AlertRouter(Telegram)
-> alert_log, using an adaptive polling schedule built around each
rule's actual firing window - not a flat poll interval.

WHY NOT A FLAT POLL INTERVAL
-----------------------------
The naive version of this script polled every active EPL/UCL fixture
every 60s, fetching all three per-fixture endpoints (fixture, events,
statistics) each time. The math on that doesn't work on the free tier:
a single 90-minute match at 60s intervals is ~90 cycles x 3 requests =
270+ requests - nearly 3x the entire 100 req/day quota, from one match.
The scanner would exhaust its quota partway through the first live game
it tracked and go dark, silently, for the rest of the day.

THE FIX: rule-window-aware polling
------------------------------------
Neither trigger can fire outside its own window - RedCardStateShiftTrigger
only evaluates minute <= minute_cutoff (~20), LateCornerCardPressureTrigger
only evaluates minute >= minute_start (~75). Between those windows,
polling accomplishes nothing regardless of what happens in the match, so
this script doesn't poll at all outside them. It also only fetches the
one or two endpoints each window's rule actually needs:
  - window1 (red card / early concede): fixture (score, minute) + events
    (red cards). Skips statistics entirely - RedCardStateShiftTrigger
    never looks at shots/corners.
  - window2 (late pressure): fixture + statistics (shots, corners).
    Skips events - no red-card check in this trigger.

Match kickoff times come from one cheap `/fixtures?date=<today>` call
per day (confirmed unrestricted on the free tier, unlike season/league-
scoped lookups), filtered client-side to EPL/UCL. Wall-clock windows
around each kickoff (config.json: window1_wallclock_minutes,
window2_start/end_offset_minutes) are intentionally generous - they only
decide *when to bother polling*; the actual TriggerRule.evaluate() calls
still gate on the real match minute returned by the API, so a loose
wall-clock window can't cause an incorrect fire, only a wasted poll.

Rough budget per tracked match: ~2 requests (odds) + ~26 (window1 @ 2
req/poll) + ~26 (window2 @ 2 req/poll) + a fraction of the 1/day
schedule call =~ 55 requests. That leaves room for one full match with
margin, or two if you shrink the windows/lengthen hot_poll_interval_seconds
in the dashboard's Settings tab. This is a real constraint of the free
tier, not something engineering alone removes - see README.

pre_match_win_probs still comes from api-football's per-fixture /odds
endpoint (de-vigged via clv_engine.power_devig()), fetched once per
match the first time it enters a window.

LateCornerCardPressureZScoreTrigger runs alongside (not instead of)
the fixed-threshold LateCornerCardPressureTrigger, reusing the same
shots_last_10min/corners_last_10min data already fetched during
window2 - no extra requests, no change to the budget math above. Its
history is maintained here (rate_history), not in the adapter, since
it's specific to this wiring layer's use of the data, not something
APIFootballAdapter itself needs to know about.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import aiohttp
from dotenv import load_dotenv

from soccer_scanner import RedCardStateShiftTrigger, LateCornerCardPressureTrigger, LateCornerCardPressureZScoreTrigger
from api_football_adapter import APIFootballAdapter, BASE_URL, _parse_fixture
from alert_dispatcher import AlertRouter, TelegramDispatcher, AlertMessage
from alert_log import log_trigger
from clv_engine import power_devig
from config_store import load_config
import scanner_manager

load_dotenv()

TARGET_LEAGUE_IDS = {39, 2}  # EPL, UCL


async def fetch_todays_target_fixtures(session: aiohttp.ClientSession) -> list[dict]:
    """One request: today's fixtures across every league, filtered
    client-side to EPL/UCL. Confirmed working on the free tier (unlike
    league+season-scoped lookups, which are blocked for the current
    season)."""
    today = datetime.now(timezone.utc).date().isoformat()
    async with session.get(f"{BASE_URL}/fixtures", params={"date": today}) as resp:
        resp.raise_for_status()
        data = await resp.json()

    fixtures = []
    for f in data.get("response", []):
        if f.get("league", {}).get("id") not in TARGET_LEAGUE_IDS:
            continue
        fixtures.append(
            {
                "id": str(f["fixture"]["id"]),
                "kickoff": datetime.fromisoformat(f["fixture"]["date"]),
                "home": f["teams"]["home"]["name"],
                "away": f["teams"]["away"]["name"],
            }
        )
    return fixtures


async def fetch_win_probs_from_odds(session: aiohttp.ClientSession, fixture_id: str, home_name: str, away_name: str) -> dict[str, float]:
    """The odds-fetching half of fetch_pre_match_win_probs(), split out
    so a market-shift re-check at trigger-time (on_trigger()) doesn't
    have to re-fetch fixture info just to re-derive team names it
    already has - one fewer wasted request per trigger fire."""
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
    return await fetch_win_probs_from_odds(session, fixture_id, home_name, away_name)


def _window_bounds(kickoff: datetime, cfg: dict) -> dict[str, tuple[datetime, datetime]]:
    return {
        "window1": (kickoff, kickoff + timedelta(minutes=cfg["window1_wallclock_minutes"])),
        "window2": (
            kickoff + timedelta(minutes=cfg["window2_start_offset_minutes"]),
            kickoff + timedelta(minutes=cfg["window2_end_offset_minutes"]),
        ),
    }


def active_window(kickoff: datetime, now: datetime, cfg: dict) -> str | None:
    for name, (start, end) in _window_bounds(kickoff, cfg).items():
        if start <= now <= end:
            return name
    return None


def next_window_start(kickoff: datetime, now: datetime, cfg: dict) -> datetime | None:
    upcoming = [start for start, _ in _window_bounds(kickoff, cfg).values() if start > now]
    return min(upcoming) if upcoming else None


async def lean_poll(session: aiohttp.ClientSession, adapter: APIFootballAdapter, fixture_id: str, window: str):
    """Fetches only the endpoint(s) the active window's rule actually
    needs, then reuses api_football_adapter's already-validated
    _parse_fixture() unchanged - it tolerates empty events/statistics
    lists fine, since APIFootballAdapter.poll() already needs to handle
    fixtures with no stats coverage (confirmed 2026-09-07)."""
    async with session.get(f"{BASE_URL}/fixtures", params={"id": fixture_id}) as resp:
        resp.raise_for_status()
        fixture_json = (await resp.json())["response"][0]

    events_json: list = []
    statistics_json: list = []
    if window == "window1":
        async with session.get(f"{BASE_URL}/fixtures/events", params={"fixture": fixture_id}) as resp:
            resp.raise_for_status()
            events_json = (await resp.json()).get("response", [])
    elif window == "window2":
        async with session.get(f"{BASE_URL}/fixtures/statistics", params={"fixture": fixture_id}) as resp:
            resp.raise_for_status()
            statistics_json = (await resp.json()).get("response", [])

    state = _parse_fixture(fixture_json, events_json, statistics_json, adapter.pre_match_win_probs)
    if window == "window2":
        adapter._update_rolling_windows(state)
    return state


async def on_trigger(event, session: aiohttp.ClientSession, fixture_id: str, home_name: str, away_name: str, pre_match_probs: dict[str, float]):
    """Captures a market-shift snapshot alongside the alert: the
    pre-match win probability (already known, free) vs. a fresh odds
    fetch taken at the exact moment the trigger fired (one extra
    request - acceptable since triggers are rare events, unlike the
    poll loop itself where every extra request multiplies across every
    cycle). This is a before/after snapshot, not a continuous line-
    movement chart - a true continuous chart would mean polling odds
    throughout the match, which would reopen the exact budget problem
    task 1 fixed. See dashboard.py's Analytics tab for how this is
    rendered."""
    try:
        live_probs = await fetch_win_probs_from_odds(session, fixture_id, home_name, away_name)
    except Exception as exc:
        print(f"[run_soccer] market-shift odds fetch failed for {fixture_id}: {exc}")
        live_probs = {}

    event.metadata["odds_pre_match"] = pre_match_probs
    event.metadata["odds_at_trigger"] = live_probs

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
    LateCornerCardPressureZScoreTrigger(
        minute_start=cfg["late_pressure_cooker_zscore"]["minute_start"],
        z_threshold=cfg["late_pressure_cooker_zscore"]["z_threshold"],
        min_history_samples=cfg["late_pressure_cooker_zscore"]["min_history_samples"],
    ),
]


async def run():
    api_key = os.environ.get("API_FOOTBALL_KEY")
    if not api_key:
        raise SystemExit("API_FOOTBALL_KEY not set - add it from the dashboard's Settings tab")

    adapter = APIFootballAdapter(api_key=api_key, pre_match_win_probs={})
    known: dict[str, dict] = {}  # fixture_id -> {kickoff, home, away, odds_fetched}
    schedule_date: object = None
    # fixture_id -> team_id -> {"shots": [...], "corners": [...]} - prior
    # shots_last_10min/corners_last_10min readings, feeds
    # LateCornerCardPressureZScoreTrigger's self-relative baseline.
    rate_history: dict[str, dict[str, dict[str, list[int]]]] = {}

    async with aiohttp.ClientSession(headers=adapter._headers()) as session:
        while True:
            scanner_manager.write_heartbeat("soccer")
            now = datetime.now(timezone.utc)

            if schedule_date != now.date():
                try:
                    fixtures = await fetch_todays_target_fixtures(session)
                    known = {f["id"]: {**f, "odds_fetched": False} for f in fixtures}
                    schedule_date = now.date()
                    print(f"[run_soccer] {len(known)} EPL/UCL fixture(s) today")
                except Exception as exc:
                    print(f"[run_soccer] schedule fetch failed, retrying in 5 min: {exc}")
                    await asyncio.sleep(300)
                    continue

            sleep_until = now + timedelta(minutes=5)  # idle cadence when nothing's in a window

            for fixture_id, info in known.items():
                window = active_window(info["kickoff"], now, cfg)
                if window is None:
                    nxt = next_window_start(info["kickoff"], now, cfg)
                    if nxt:
                        sleep_until = min(sleep_until, nxt)
                    continue

                try:
                    if not info["odds_fetched"]:
                        probs = await fetch_pre_match_win_probs(session, fixture_id)
                        adapter.pre_match_win_probs.update(probs)
                        info["odds_fetched"] = True

                    state = await lean_poll(session, adapter, fixture_id, window)

                    if window == "window2":
                        history = rate_history.setdefault(fixture_id, {})
                        for team in (state.home, state.away):
                            team_hist = history.setdefault(team.team_id, {"shots": [], "corners": []})
                            team.shots_10min_history = list(team_hist["shots"])
                            team.corners_10min_history = list(team_hist["corners"])
                            team_hist["shots"].append(team.shots_last_10min)
                            team_hist["corners"].append(team.corners_last_10min)

                    for rule in rules:
                        event = rule.evaluate(state)
                        if event:
                            await on_trigger(event, session, fixture_id, info["home"], info["away"], dict(adapter.pre_match_win_probs))
                except Exception as exc:
                    # one fixture's bad response shouldn't kill the scanner
                    print(f"[run_soccer] poll failed for {fixture_id} ({window}): {exc}")

                sleep_until = min(sleep_until, now + timedelta(seconds=cfg["hot_poll_interval_seconds"]))

            await asyncio.sleep(max(1.0, (sleep_until - now).total_seconds()))


if __name__ == "__main__":
    print("Starting soccer scanner (EPL/UCL). Ctrl+C to stop.")
    asyncio.run(run())
