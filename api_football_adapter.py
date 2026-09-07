"""
api_football_adapter.py

Real adapter against api-sports.io's Football API (api-football), for
soccer_scanner.py. Needs a free API key from dashboard.api-football.com
(100 requests/day on the free tier, every endpoint including live
events and statistics - just volume-capped).

    live fixtures: https://v3.football.api-sports.io/fixtures?live=all
    events:        https://v3.football.api-sports.io/fixtures/events?fixture={id}
    statistics:    https://v3.football.api-sports.io/fixtures/statistics?fixture={id}

Auth header: x-apisports-key: YOUR_KEY

IMPORTANT - same caveat as the ESPN adapter: I built and unit-tested
the parsing logic against api-football's documented response shape,
but couldn't hit the live endpoint from the sandbox this was written
in (no network route to api-sports.io there). Run with --debug during
a live match to dump the raw JSON and check field paths.

Pre-match win probabilities aren't something api-football gives you
de-vigged for free, so you supply them yourself - pull pre-match odds
from The Odds API (or wherever), run them through clv_engine.power_devig(),
and pass the result in as pre_match_win_probs={team_name: prob}.

Rolling 10-minute shot/corner windows: the adapter keeps its own
in-memory history of (elapsed_minute, cumulative_shots, cumulative_corners)
samples per fixture and per team, and computes the delta against the
oldest sample still inside the trailing 10-minute window on each poll.
That history resets if you restart the process - fine for a single
long-running scanner, not something to rely on across restarts.
"""

from __future__ import annotations

import argparse
import asyncio
import json

import aiohttp

from soccer_scanner import SoccerGameState, TeamMatchState

BASE_URL = "https://v3.football.api-sports.io"


def _stat_value(statistics: list[dict], stat_type: str) -> int:
    """statistics is api-football's per-team 'statistics' array:
    [{'type': 'Shots on Goal', 'value': 4}, {'type': 'Corner Kicks', 'value': 3}, ...]
    Returns 0 for null/missing values instead of raising."""
    for entry in statistics:
        if entry.get("type") == stat_type:
            return entry.get("value") or 0
    return 0


def _parse_fixture(
    fixture_json: dict,
    events_json: list[dict],
    statistics_json: list[dict],
    pre_match_win_probs: dict[str, float],
) -> SoccerGameState:
    """Pure function, no network - unit-testable against saved fixtures."""
    fixture = fixture_json["fixture"]
    teams = fixture_json["teams"]
    goals = fixture_json["goals"]

    home_name, away_name = teams["home"]["name"], teams["away"]["name"]
    home_id, away_id = teams["home"]["id"], teams["away"]["id"]

    home = TeamMatchState(team_id=str(home_id), name=home_name, pre_match_win_prob=pre_match_win_probs.get(home_name, 0.5))
    away = TeamMatchState(team_id=str(away_id), name=away_name, pre_match_win_prob=pre_match_win_probs.get(away_name, 0.5))

    for event in events_json:
        if event.get("type") != "Card" or event.get("detail") != "Red Card":
            continue
        team_id = event.get("team", {}).get("id")
        if team_id == home_id:
            home.red_cards += 1
        elif team_id == away_id:
            away.red_cards += 1

    for team_stats in statistics_json:
        team_id = team_stats.get("team", {}).get("id")
        stats = team_stats.get("statistics", [])
        shots = _stat_value(stats, "Shots on Goal") + _stat_value(stats, "Shots off Goal")
        corners = _stat_value(stats, "Corner Kicks")
        if team_id == home_id:
            home.shots, home.corners = shots, corners
        elif team_id == away_id:
            away.shots, away.corners = shots, corners

    return SoccerGameState(
        game_id=str(fixture["id"]),
        home=home,
        away=away,
        minute=fixture["status"].get("elapsed") or 0,
        home_score=goals.get("home") or 0,
        away_score=goals.get("away") or 0,
    )


class APIFootballAdapter:
    def __init__(self, api_key: str, pre_match_win_probs: dict[str, float] | None = None, rolling_window_minutes: int = 10):
        self.api_key = api_key
        self.pre_match_win_probs = pre_match_win_probs or {}
        self.rolling_window_minutes = rolling_window_minutes
        # game_id -> team_id -> list of (elapsed_minute, cumulative_shots, cumulative_corners)
        self._history: dict[str, dict[str, list[tuple[int, int, int]]]] = {}

    def _headers(self) -> dict[str, str]:
        return {"x-apisports-key": self.api_key}

    async def active_games(self) -> list[str]:
        async with aiohttp.ClientSession(headers=self._headers()) as session:
            async with session.get(f"{BASE_URL}/fixtures", params={"live": "all"}) as resp:
                resp.raise_for_status()
                data = await resp.json()
        return [str(f["fixture"]["id"]) for f in data.get("response", [])]

    async def poll(self, game_id: str) -> SoccerGameState:
        async with aiohttp.ClientSession(headers=self._headers()) as session:
            async with session.get(f"{BASE_URL}/fixtures", params={"id": game_id}) as resp:
                resp.raise_for_status()
                fixture_data = await resp.json()
            fixture_json = fixture_data["response"][0]

            async with session.get(f"{BASE_URL}/fixtures/events", params={"fixture": game_id}) as resp:
                resp.raise_for_status()
                events_json = (await resp.json()).get("response", [])

            async with session.get(f"{BASE_URL}/fixtures/statistics", params={"fixture": game_id}) as resp:
                resp.raise_for_status()
                statistics_json = (await resp.json()).get("response", [])

        state = _parse_fixture(fixture_json, events_json, statistics_json, self.pre_match_win_probs)
        self._update_rolling_windows(state)
        return state

    def _update_rolling_windows(self, state: SoccerGameState) -> None:
        history = self._history.setdefault(state.game_id, {})
        for team in (state.home, state.away):
            samples = history.setdefault(team.team_id, [])
            samples.append((state.minute, team.shots, team.corners))

            cutoff = state.minute - self.rolling_window_minutes
            samples[:] = [s for s in samples if s[0] >= cutoff] or samples[-1:]
            baseline_minute, baseline_shots, baseline_corners = samples[0]

            team.shots_last_10min = max(0, team.shots - baseline_shots)
            team.corners_last_10min = max(0, team.corners - baseline_corners)


async def _debug_dump(fixture_id: str, api_key: str):
    """Run: python api_football_adapter.py --debug <fixture_id> --key <your_key>
    Fetches the real fixture/events/statistics, saves each to
    api_football_debug_*.json, and prints the parsed SoccerGameState."""
    adapter = APIFootballAdapter(api_key=api_key)
    async with aiohttp.ClientSession(headers=adapter._headers()) as session:
        async with session.get(f"{BASE_URL}/fixtures", params={"id": fixture_id}) as resp:
            fixture_data = await resp.json()
        async with session.get(f"{BASE_URL}/fixtures/events", params={"fixture": fixture_id}) as resp:
            events_data = await resp.json()
        async with session.get(f"{BASE_URL}/fixtures/statistics", params={"fixture": fixture_id}) as resp:
            stats_data = await resp.json()

    for name, payload in [("fixture", fixture_data), ("events", events_data), ("statistics", stats_data)]:
        with open(f"api_football_debug_{name}.json", "w") as f:
            json.dump(payload, f, indent=2)

    try:
        state = _parse_fixture(fixture_data["response"][0], events_data.get("response", []), stats_data.get("response", []), {})
        print(state)
    except Exception as exc:
        print(f"Parsing failed: {exc}")
        print("Raw JSON saved to api_football_debug_*.json - check it against the field paths in _parse_fixture()")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", metavar="FIXTURE_ID", help="fetch + dump a real live fixture for field-path debugging")
    parser.add_argument("--key", help="your api-football key (required with --debug)")
    args = parser.parse_args()

    if args.debug:
        if not args.key:
            raise SystemExit("--key is required with --debug")
        asyncio.run(_debug_dump(args.debug, args.key))
    else:
        print("Run with --debug <fixture_id> --key <your_key> during a live match to test parsing against real data.")
        print("Find a live fixture_id by hitting https://v3.football.api-sports.io/fixtures?live=all with your key.")
