"""
entity_resolution.py

Cleans up the mismatched naming schemes you get across box score feeds,
odds APIs, and injury reports. Two recurring problems:

1. Team names/abbreviations don't line up across vendors
   ("76ers" vs "PHI" vs "Philadelphia 76ers" vs "Sixers").
2. Player names get truncated or reformatted differently by different
   vendors ("M. Salah" vs "Mohamed Salah" vs "Salah, Mohamed").

This module gives you a small, dependency-light resolver for both,
plus a timestamp normalizer so every feed lands on the same UTC
ISO8601 format before it hits your GameState objects.

Team alias tables are intentionally partial (30 NBA teams is a closed
list so that one's complete; soccer is a representative sample). Load
your own YAML/CSV of aliases for full league coverage and merge it
into the dicts below at startup - the resolve_team() function doesn't
care where the alias table came from.
"""

from __future__ import annotations

import re
import difflib
from datetime import datetime, timezone
from typing import Optional

from dateutil import parser as dateparser

# ---------------------------------------------------------------------------
# Team alias tables
# ---------------------------------------------------------------------------

NBA_TEAMS: dict[str, list[str]] = {
    "ATL": ["atlanta hawks", "hawks"],
    "BOS": ["boston celtics", "celtics"],
    "BKN": ["brooklyn nets", "nets"],
    "CHA": ["charlotte hornets", "hornets"],
    "CHI": ["chicago bulls", "bulls"],
    "CLE": ["cleveland cavaliers", "cavaliers", "cavs"],
    "DAL": ["dallas mavericks", "mavericks", "mavs"],
    "DEN": ["denver nuggets", "nuggets"],
    "DET": ["detroit pistons", "pistons"],
    "GSW": ["golden state warriors", "warriors", "dubs"],
    "HOU": ["houston rockets", "rockets"],
    "IND": ["indiana pacers", "pacers"],
    "LAC": ["la clippers", "los angeles clippers", "clippers"],
    "LAL": ["la lakers", "los angeles lakers", "lakers"],
    "MEM": ["memphis grizzlies", "grizzlies"],
    "MIA": ["miami heat", "heat"],
    "MIL": ["milwaukee bucks", "bucks"],
    "MIN": ["minnesota timberwolves", "timberwolves", "wolves"],
    "NOP": ["new orleans pelicans", "pelicans"],
    "NYK": ["new york knicks", "knicks"],
    "OKC": ["oklahoma city thunder", "thunder"],
    "ORL": ["orlando magic", "magic"],
    "PHI": ["philadelphia 76ers", "76ers", "sixers"],
    "PHX": ["phoenix suns", "suns"],
    "POR": ["portland trail blazers", "trail blazers", "blazers"],
    "SAC": ["sacramento kings", "kings"],
    "SAS": ["san antonio spurs", "spurs"],
    "TOR": ["toronto raptors", "raptors"],
    "UTA": ["utah jazz", "jazz"],
    "WAS": ["washington wizards", "wizards"],
}

# Representative soccer sample: full EPL + a handful of recurring UCL sides.
# Extend from a config file for full top-5-league + UCL coverage.
SOCCER_TEAMS: dict[str, list[str]] = {
    "ARS": ["arsenal", "arsenal fc"],
    "AVL": ["aston villa", "aston villa fc", "villa"],
    "BOU": ["bournemouth", "afc bournemouth"],
    "BRE": ["brentford", "brentford fc"],
    "BHA": ["brighton", "brighton and hove albion", "brighton hove albion"],
    "CHE": ["chelsea", "chelsea fc"],
    "CRY": ["crystal palace", "palace"],
    "EVE": ["everton", "everton fc"],
    "FUL": ["fulham", "fulham fc"],
    "IPS": ["ipswich town", "ipswich"],
    "LEI": ["leicester city", "leicester", "foxes"],
    "LIV": ["liverpool", "liverpool fc", "lfc"],
    "MCI": ["manchester city", "man city", "mcfc"],
    "MUN": ["manchester united", "man united", "man utd", "mufc"],
    "NEW": ["newcastle united", "newcastle", "nufc"],
    "NFO": ["nottingham forest", "forest"],
    "SOU": ["southampton", "saints"],
    "TOT": ["tottenham hotspur", "tottenham", "spurs"],
    "WHU": ["west ham united", "west ham"],
    "WOL": ["wolverhampton wanderers", "wolves fc", "wolverhampton"],
    "RMA": ["real madrid", "real madrid cf"],
    "FCB": ["barcelona", "fc barcelona", "barca"],
    "BAY": ["bayern munich", "fc bayern munich", "bayern"],
    "PSG": ["paris saint-germain", "psg", "paris sg"],
    "INT": ["inter milan", "internazionale", "inter"],
    "JUV": ["juventus", "juventus fc", "juve"],
}


def resolve_team(name: str, league_map: dict[str, list[str]], cutoff: float = 0.6) -> Optional[str]:
    """
    Resolves a team name/nickname/abbreviation to its canonical
    abbreviation using the given league alias table. Falls back to
    fuzzy matching against every alias if there's no exact hit, so
    small vendor-specific variants ("Man Utd" vs "Man United") still
    land on the right team.
    """
    norm = name.strip().lower()

    for abbr, aliases in league_map.items():
        if norm == abbr.lower() or norm in aliases:
            return abbr

    all_aliases: list[str] = []
    alias_to_abbr: dict[str, str] = {}
    for abbr, aliases in league_map.items():
        for alias in aliases:
            all_aliases.append(alias)
            alias_to_abbr[alias] = abbr

    match = difflib.get_close_matches(norm, all_aliases, n=1, cutoff=cutoff)
    return alias_to_abbr[match[0]] if match else None


# ---------------------------------------------------------------------------
# Player resolution
# ---------------------------------------------------------------------------

def normalize_player_name(name: str) -> str:
    """
    Strips periods and collapses whitespace so names compare cleanly.
    Also flips "Last, First" into "First Last" order, since that's a
    common vendor format and the rest of this module assumes
    first-name-first.
    """
    if "," in name:
        last, _, first = name.partition(",")
        name = f"{first.strip()} {last.strip()}"
    name = name.replace(".", " ").strip()
    return re.sub(r"\s+", " ", name).lower()


def resolve_player(name: str, roster: list[str], cutoff: float = 0.6) -> Optional[str]:
    """
    Matches a possibly-abbreviated name ("M. Salah") against a known
    roster ("Mohamed Salah"). Tries last-name + first-initial first,
    since that's the most common vendor shorthand, then falls back to
    fuzzy matching on the full string for anything that doesn't hit.

    Returns the roster entry it matched to, or None if nothing clears
    the bar. When the initial+lastname pass finds more than one
    candidate (e.g. two players who share a surname and first initial),
    it's left ambiguous and handed to the fuzzy pass instead of
    guessing.
    """
    target_parts = normalize_player_name(name).split()
    if not target_parts:
        return None
    target_last = target_parts[-1]
    target_first_initial = target_parts[0][0]

    candidates = []
    for full_name in roster:
        parts = normalize_player_name(full_name).split()
        if not parts:
            continue
        last, first_initial = parts[-1], parts[0][0]
        if last == target_last and first_initial == target_first_initial:
            candidates.append(full_name)

    if len(candidates) == 1:
        return candidates[0]

    normalized_roster = [normalize_player_name(r) for r in roster]
    matches = difflib.get_close_matches(normalize_player_name(name), normalized_roster, n=1, cutoff=cutoff)
    if matches:
        return roster[normalized_roster.index(matches[0])]
    return None


# ---------------------------------------------------------------------------
# Timestamp normalization
# ---------------------------------------------------------------------------

def normalize_timestamp(raw: str) -> str:
    """
    Parses whatever timestamp format a feed hands you (ISO8601 with
    offset, US-style strings, unix-ish strings dateutil can figure
    out) and returns a UTC ISO8601 string. Naive timestamps are
    assumed to already be UTC - override upstream if a specific feed
    uses local time.
    """
    dt = dateparser.parse(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


if __name__ == "__main__":
    # quick smoke test
    assert resolve_team("76ers", NBA_TEAMS) == "PHI"
    assert resolve_team("Sixers", NBA_TEAMS) == "PHI"
    assert resolve_team("Philadelphia 76ers", NBA_TEAMS) == "PHI"
    assert resolve_team("Man Utd", SOCCER_TEAMS) == "MUN"
    assert resolve_team("Spurs", SOCCER_TEAMS) == "TOT"  # NBA has a "Spurs" too - scope by league_map

    roster = ["Mohamed Salah", "Virgil van Dijk", "Alisson Becker"]
    assert resolve_player("M. Salah", roster) == "Mohamed Salah"
    assert resolve_player("Salah, Mohamed", roster) == "Mohamed Salah"

    ts = normalize_timestamp("2026-01-15 19:30:00")
    print("normalize_timestamp ->", ts)
    print("All entity_resolution smoke tests passed.")
