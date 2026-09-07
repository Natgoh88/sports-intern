from entity_resolution import (
    NBA_TEAMS,
    SOCCER_TEAMS,
    resolve_team,
    resolve_player,
    normalize_player_name,
    normalize_timestamp,
)


def test_resolve_team_exact_and_alias():
    assert resolve_team("76ers", NBA_TEAMS) == "PHI"
    assert resolve_team("Sixers", NBA_TEAMS) == "PHI"
    assert resolve_team("Philadelphia 76ers", NBA_TEAMS) == "PHI"
    assert resolve_team("PHI", NBA_TEAMS) == "PHI"


def test_resolve_team_soccer_variants():
    assert resolve_team("Man Utd", SOCCER_TEAMS) == "MUN"
    assert resolve_team("Spurs", SOCCER_TEAMS) == "TOT"


def test_resolve_team_unknown_returns_none():
    assert resolve_team("Definitely Not A Team", NBA_TEAMS) is None


def test_normalize_player_name_handles_last_comma_first():
    assert normalize_player_name("Salah, Mohamed") == "mohamed salah"


def test_resolve_player_initial_and_full_name():
    roster = ["Mohamed Salah", "Virgil van Dijk", "Alisson Becker"]
    assert resolve_player("M. Salah", roster) == "Mohamed Salah"
    assert resolve_player("Salah, Mohamed", roster) == "Mohamed Salah"


def test_normalize_timestamp_naive_assumed_utc():
    ts = normalize_timestamp("2026-01-15 19:30:00")
    assert ts == "2026-01-15T19:30:00+00:00"
