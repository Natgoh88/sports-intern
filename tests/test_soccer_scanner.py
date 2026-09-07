from soccer_scanner import (
    TeamMatchState,
    SoccerGameState,
    RedCardStateShiftTrigger,
    LateCornerCardPressureTrigger,
)


def make_state(home_prob=0.72, away_prob=0.10, **overrides) -> SoccerGameState:
    home = TeamMatchState(team_id="MCI", name="Man City", pre_match_win_prob=home_prob)
    away = TeamMatchState(team_id="BOU", name="Bournemouth", pre_match_win_prob=away_prob)
    defaults = dict(game_id="g1", home=home, away=away, minute=5)
    defaults.update(overrides)
    return SoccerGameState(**defaults)


def test_red_card_trigger_fires_when_favorite_goes_down_a_man():
    state = make_state(minute=18)
    state.home.red_cards = 1

    event = RedCardStateShiftTrigger(favorite_prob_threshold=0.55).evaluate(state)

    assert event is not None
    assert "10 men" in event.message


def test_red_card_trigger_fires_when_favorite_concedes_early():
    state = make_state(minute=15)
    state.away_score = 1  # home (favorite) is conceding

    event = RedCardStateShiftTrigger(favorite_prob_threshold=0.55).evaluate(state)

    assert event is not None
    assert "conceded" in event.message


def test_red_card_trigger_ignores_underdog_state_changes():
    state = make_state(minute=15)
    state.away.red_cards = 1  # underdog going down a man, not the favorite

    assert RedCardStateShiftTrigger(favorite_prob_threshold=0.55).evaluate(state) is None


def test_red_card_trigger_does_not_fire_after_minute_cutoff():
    state = make_state(minute=45)
    state.home.red_cards = 1

    assert RedCardStateShiftTrigger(favorite_prob_threshold=0.55, minute_cutoff=20).evaluate(state) is None


def test_late_pressure_trigger_fires_for_trailing_favorite_spiking_late():
    state = make_state(minute=80)
    state.away_score = 1
    state.home.shots_last_10min = 4

    event = LateCornerCardPressureTrigger().evaluate(state)

    assert event is not None
    assert event.rule_name == "late_pressure_cooker"


def test_late_pressure_trigger_ignores_before_minute_start():
    state = make_state(minute=60)
    state.away_score = 1
    state.home.shots_last_10min = 4

    assert LateCornerCardPressureTrigger(minute_start=75).evaluate(state) is None


def test_late_pressure_trigger_ignores_team_trailing_by_more_than_one():
    state = make_state(minute=80)
    state.away_score = 2
    state.home.shots_last_10min = 4

    assert LateCornerCardPressureTrigger().evaluate(state) is None
