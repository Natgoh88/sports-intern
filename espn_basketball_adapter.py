"""
espn_basketball_adapter.py

Real PlayByPlayAdapter implementation against ESPN's hidden (undocumented)
endpoints. No API key needed.

    scoreboard: https://site.api.espn.com/apis/site/v2/sports/basketball/{league}/scoreboard
    summary:    https://site.api.espn.com/apis/site/v2/sports/basketball/{league}/summary?event={id}

league is "nba" or "mens-college-basketball".

IMPORTANT - this is unofficial and undocumented. I could not test this
against the live endpoint from the sandbox I built it in (no network
route to espn.com from there), so the JSON parsing below is written
against ESPN's well-documented general shape (header/boxscore/plays)
but hasn't been run against a real in-progress game. Run
`python espn_basketball_adapter.py --debug <event_id>` during a live
game and it'll dump the raw JSON next to the parsed GameState so you
can fix any field path that's drifted.

What it does:
- active_games(): pulls the scoreboard, returns event ids currently "in progress"
- poll(game_id): pulls that event's summary, and reconstructs team/period
  fouls by walking the play-by-play list and counting "Foul" plays,
  since ESPN's boxscore totals aren't split by period.

You supply a `key_players` map so FoulTroubleTrigger knows who to watch:
    key_players = {
        "PHI": {"Joel Embiid": "rim_protector"},
        "BOS": {"Jayson Tatum": "primary_scorer"},
    }
Match on team abbreviation and exact ESPN athlete displayName.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys

import aiohttp

from basketball_scanner import GameState, League, PlayerFoulState, TeamState

BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball"


def _seconds_from_clock(display_clock: str) -> int:
    """'6:42' -> 402 seconds. Falls back to 0 on anything unparsable."""
    match = re.match(r"^(\d+):(\d+)", display_clock or "")
    if not match:
        return 0
    minutes, seconds = int(match.group(1)), int(match.group(2))
    return minutes * 60 + seconds


def _parse_summary(data: dict, game_id: str, league: League, key_players: dict[str, dict[str, str]]) -> GameState:
    """Pure function, no network - easy to unit test against a saved fixture."""
    header = data["header"]
    competition = header["competitions"][0]
    status = competition["status"]

    competitors = {c["homeAway"]: c for c in competition["competitors"]}
    home_c, away_c = competitors["home"], competitors["away"]

    def make_team(c: dict) -> TeamState:
        abbr = c["team"]["abbreviation"]
        return TeamState(
            team_id=abbr,
            name=c["team"].get("displayName", abbr),
            players={},  # filled in below from play-by-play foul counts
        )

    home, away = make_team(home_c), make_team(away_c)
    team_by_espn_id = {home_c["team"]["id"]: home, away_c["team"]["id"]: away}

    state = GameState(
        game_id=game_id,
        league=league,
        home=home,
        away=away,
        period=status.get("period", 1),
        clock_seconds_remaining=_seconds_from_clock(status.get("displayClock", "0:00")),
        home_score=int(home_c.get("score", 0)),
        away_score=int(away_c.get("score", 0)),
    )

    # Walk play-by-play, tally fouls per team for the *current* period,
    # and cumulative fouls per player (ESPN doesn't give a running
    # per-player foul count field directly, so we count "Foul" plays
    # that name a participant).
    plays = data.get("plays", [])
    for play in plays:
        play_type_text = (play.get("type", {}) or {}).get("text", "") or ""
        if "foul" not in play_type_text.lower():
            continue

        play_period = (play.get("period", {}) or {}).get("number")
        team_espn_id = (play.get("team", {}) or {}).get("id")
        team = team_by_espn_id.get(team_espn_id)
        if team is None:
            continue

        if play_period == state.period:
            team.period_fouls += 1

        for participant in play.get("participants", []) or []:
            athlete = participant.get("athlete", {}) or {}
            name = athlete.get("displayName")
            athlete_id = athlete.get("id")
            if not name or not athlete_id:
                continue
            if athlete_id not in team.players:
                role = key_players.get(team.team_id, {}).get(name, "")
                team.players[athlete_id] = PlayerFoulState(
                    player_id=athlete_id,
                    name=name,
                    is_key_player=name in key_players.get(team.team_id, {}),
                    role=role,
                )
            team.players[athlete_id].fouls += 1

    return state


class ESPNBasketballAdapter:
    def __init__(self, league: League = League.NBA, key_players: dict[str, dict[str, str]] | None = None):
        self.league = league
        self.key_players = key_players or {}
        self._espn_league_slug = "nba" if league == League.NBA else "mens-college-basketball"

    async def active_games(self) -> list[str]:
        url = f"{BASE_URL}/{self._espn_league_slug}/scoreboard"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                resp.raise_for_status()
                data = await resp.json()

        game_ids = []
        for event in data.get("events", []):
            state = (event.get("status", {}).get("type", {}) or {}).get("state")
            if state == "in":  # ESPN's convention: "in" = in progress
                game_ids.append(event["id"])
        return game_ids

    async def poll(self, game_id: str) -> GameState:
        url = f"{BASE_URL}/{self._espn_league_slug}/summary"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params={"event": game_id}) as resp:
                resp.raise_for_status()
                data = await resp.json()
        return _parse_summary(data, game_id, self.league, self.key_players)


async def _debug_dump(event_id: str):
    """Run: python espn_basketball_adapter.py --debug <event_id>
    Fetches the real summary, prints the parsed GameState, and saves the
    raw JSON to espn_debug_<event_id>.json so you can fix field paths
    against what ESPN is actually returning right now."""
    adapter = ESPNBasketballAdapter()
    url = f"{BASE_URL}/nba/summary"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params={"event": event_id}) as resp:
            data = await resp.json()
    with open(f"espn_debug_{event_id}.json", "w") as f:
        json.dump(data, f, indent=2)
    try:
        state = _parse_summary(data, event_id, League.NBA, {})
        print(state)
    except Exception as exc:
        print(f"Parsing failed: {exc}")
        print(f"Raw JSON saved to espn_debug_{event_id}.json - check it against the field paths in _parse_summary()")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", metavar="EVENT_ID", help="fetch + dump a real event for field-path debugging")
    args = parser.parse_args()

    if args.debug:
        asyncio.run(_debug_dump(args.debug))
    else:
        print("Run with --debug <espn_event_id> during a live game to test parsing against real data.")
        print("Find an event_id from https://www.espn.com/nba/scoreboard - it's the numeric id in the game URL.")
        sys.exit(0)
