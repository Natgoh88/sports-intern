from basketball_scanner import (
    League,
    TeamState,
    PlayerFoulState,
    GameState,
    BonusTrigger,
    FoulTroubleTrigger,
)


def make_game_state(**overrides) -> GameState:
    home = TeamState(team_id="PHI", name="76ers")
    away = TeamState(team_id="BOS", name="Celtics")
    defaults = dict(
        game_id="g1",
        league=League.NBA,
        home=home,
        away=away,
        period=1,
        clock_seconds_remaining=720,
    )
    defaults.update(overrides)
    return GameState(**defaults)


def test_bonus_trigger_fires_when_both_teams_in_bonus_with_time_left():
    state = make_game_state()
    state.home.period_fouls = 5
    state.away.period_fouls = 5

    event = BonusTrigger(min_seconds_remaining=300).evaluate(state)

    assert event is not None
    assert event.rule_name == "bonus_trigger"
    assert "bonus" in event.message.lower()


def test_bonus_trigger_does_not_fire_with_one_team_short():
    state = make_game_state()
    state.home.period_fouls = 5
    state.away.period_fouls = 4

    assert BonusTrigger().evaluate(state) is None


def test_bonus_trigger_does_not_fire_too_late_in_period():
    state = make_game_state(clock_seconds_remaining=100)
    state.home.period_fouls = 5
    state.away.period_fouls = 5

    assert BonusTrigger(min_seconds_remaining=300).evaluate(state) is None


def test_bonus_trigger_dedupes_same_period():
    state = make_game_state()
    state.home.period_fouls = 5
    state.away.period_fouls = 5
    trigger = BonusTrigger(min_seconds_remaining=300)

    assert trigger.evaluate(state) is not None
    assert trigger.evaluate(state) is None  # already fired for this period


def test_foul_trouble_trigger_fires_for_tagged_key_player():
    state = make_game_state()
    state.home.players["p1"] = PlayerFoulState(
        player_id="p1", name="Joel Embiid", fouls=2, is_key_player=True, role="rim_protector"
    )

    event = FoulTroubleTrigger(foul_count_threshold=2).evaluate(state)

    assert event is not None
    assert event.rule_name == "foul_trouble_trigger"
    assert "Joel Embiid" in event.message


def test_foul_trouble_trigger_ignores_non_key_players():
    state = make_game_state()
    state.home.players["p2"] = PlayerFoulState(player_id="p2", name="Bench Guy", fouls=3, is_key_player=False)

    assert FoulTroubleTrigger().evaluate(state) is None


def test_foul_trouble_trigger_does_not_fire_after_cutoff_period():
    state = make_game_state(period=2)
    state.home.players["p1"] = PlayerFoulState(player_id="p1", name="Joel Embiid", fouls=2, is_key_player=True)

    assert FoulTroubleTrigger(early_period_cutoff=1).evaluate(state) is None
