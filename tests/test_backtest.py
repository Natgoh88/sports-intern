from backtest import reconstruct_minute_states, backtest_fixture, _pre_match_win_probs_from_odds


def _fixture_json(fixture_id=1, home_id=10, away_id=20, home_name="Man City", away_name="Bournemouth", home_goals=0, away_goals=1, elapsed=90):
    return {
        "fixture": {"id": fixture_id, "status": {"elapsed": elapsed}},
        "teams": {
            "home": {"id": home_id, "name": home_name},
            "away": {"id": away_id, "name": away_name},
        },
        "goals": {"home": home_goals, "away": away_goals},
    }


def _odds_json(home_odd=1.4, draw_odd=4.8, away_odd=7.0):
    return {
        "response": [
            {
                "bookmakers": [
                    {
                        "bets": [
                            {
                                "name": "Match Winner",
                                "values": [
                                    {"value": "Home", "odd": str(home_odd)},
                                    {"value": "Draw", "odd": str(draw_odd)},
                                    {"value": "Away", "odd": str(away_odd)},
                                ],
                            }
                        ]
                    }
                ]
            }
        ]
    }


def test_pre_match_win_probs_from_odds_extracts_and_devigs():
    probs = _pre_match_win_probs_from_odds(_odds_json(), "Man City", "Bournemouth")
    assert "Man City" in probs and "Bournemouth" in probs
    assert probs["Man City"] > probs["Bournemouth"]


def test_pre_match_win_probs_from_odds_empty_when_no_market():
    assert _pre_match_win_probs_from_odds({"response": []}, "A", "B") == {}


def test_reconstruct_minute_states_tracks_red_card_and_score():
    fixture_json = _fixture_json(home_goals=0, away_goals=1)
    events = [
        {"time": {"elapsed": 15}, "team": {"id": 10}, "type": "Card", "detail": "Red Card"},
        {"time": {"elapsed": 30}, "team": {"id": 20}, "type": "Goal"},
    ]
    states = reconstruct_minute_states(fixture_json, events, {"Man City": 0.72, "Bournemouth": 0.10})

    before = states[10]
    after_card = states[16]
    after_goal = states[31]

    assert before.home.red_cards == 0
    assert after_card.home.red_cards == 1
    assert after_goal.away_score == 1
    assert after_goal.home_score == 0


def test_backtest_fixture_detects_favorite_red_card_and_judges_outcome_correctly():
    # Man City (heavy favorite) goes down to 10 men in minute 15, match
    # ends 0-1 to Bournemouth - the suggested side (Bournemouth) DID win
    fixture_json = _fixture_json(home_goals=0, away_goals=1)
    events = [{"time": {"elapsed": 15}, "team": {"id": 10}, "type": "Card", "detail": "Red Card"}]

    fires = backtest_fixture(fixture_json, events, _odds_json(home_odd=1.4, draw_odd=4.8, away_odd=7.0))

    assert len(fires) == 1
    assert fires[0].favorite_team == "Man City"
    assert fires[0].suggested_side == "Bournemouth"
    assert fires[0].would_have_won is True


def test_backtest_fixture_judges_a_loss_correctly():
    # same red card, but City hang on and win anyway 2-1 - the
    # suggested side (Bournemouth) did NOT win or draw
    fixture_json = _fixture_json(home_goals=2, away_goals=1)
    events = [{"time": {"elapsed": 15}, "team": {"id": 10}, "type": "Card", "detail": "Red Card"}]

    fires = backtest_fixture(fixture_json, events, _odds_json())

    assert len(fires) == 1
    assert fires[0].would_have_won is False


def test_backtest_fixture_no_fires_when_no_early_red_card():
    fixture_json = _fixture_json(home_goals=1, away_goals=0)
    events: list = []

    fires = backtest_fixture(fixture_json, events, _odds_json())

    assert fires == []


def test_backtest_fixture_ignores_underdog_going_down_a_man():
    # Bournemouth (underdog) gets the red card, not the favorite -
    # RedCardStateShiftTrigger only cares about the favorite's state
    fixture_json = _fixture_json(home_goals=2, away_goals=0)
    events = [{"time": {"elapsed": 15}, "team": {"id": 20}, "type": "Card", "detail": "Red Card"}]

    fires = backtest_fixture(fixture_json, events, _odds_json())

    assert fires == []
