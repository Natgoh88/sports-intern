from soccer_scanner import (
    TeamMatchState,
    SoccerGameState,
    RedCardStateShiftTrigger,
    LateCornerCardPressureTrigger,
    LateCornerCardPressureZScoreTrigger,
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


def test_zscore_trigger_requires_min_history_before_firing():
    state = make_state(minute=80)
    state.away_score = 1
    state.home.shots_last_10min = 10
    state.home.shots_10min_history = [1, 1]  # only 2 samples, needs 3 by default

    assert LateCornerCardPressureZScoreTrigger().evaluate(state) is None


def test_zscore_trigger_fires_on_real_outlier_vs_own_baseline():
    state = make_state(minute=80)
    state.away_score = 1
    state.home.shots_10min_history = [1, 2, 1, 2]  # quiet team all match
    state.home.shots_last_10min = 8  # suddenly not quiet

    event = LateCornerCardPressureZScoreTrigger().evaluate(state)

    assert event is not None
    assert event.rule_name == "late_pressure_cooker_zscore"
    assert event.metadata["shot_z"] > 1.5


def test_zscore_trigger_does_not_fire_when_rate_matches_own_baseline():
    state = make_state(minute=80)
    state.away_score = 1
    state.home.shots_10min_history = [4, 5, 4, 5]  # a team that's always been busy
    state.home.shots_last_10min = 5  # right in line with its own average

    assert LateCornerCardPressureZScoreTrigger().evaluate(state) is None


def test_zscore_trigger_handles_zero_variance_baseline_without_crashing():
    state = make_state(minute=80)
    state.away_score = 1
    state.home.shots_10min_history = [3, 3, 3]  # zero variance - stdev is 0
    state.home.shots_last_10min = 6

    # can't compute a meaningful z-score against a zero-variance baseline -
    # should decline to fire rather than divide by zero
    assert LateCornerCardPressureZScoreTrigger().evaluate(state) is None


def test_zscore_trigger_dedupes_same_team():
    state = make_state(minute=80)
    state.away_score = 1
    state.home.shots_10min_history = [1, 2, 1]  # nonzero variance, needed for a defined z-score
    state.home.shots_last_10min = 10
    trigger = LateCornerCardPressureZScoreTrigger()

    assert trigger.evaluate(state) is not None
    assert trigger.evaluate(state) is None  # already fired for this team
