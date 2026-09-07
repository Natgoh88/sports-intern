import pytest

from clv_engine import (
    implied_prob,
    multiplicative_devig,
    power_devig,
    calculate_clv_pct,
    bootstrap_ci,
    BetLogger,
    MIN_SAMPLES_FOR_CI,
)


def test_implied_prob():
    assert implied_prob(2.0) == pytest.approx(0.5)


def test_multiplicative_devig_symmetric_market():
    fair = multiplicative_devig([1.909, 1.909])
    assert fair[0] == pytest.approx(0.5, abs=1e-6)
    assert fair[1] == pytest.approx(0.5, abs=1e-6)


def test_power_devig_symmetric_market():
    fair = power_devig([1.909, 1.909])
    assert fair[0] == pytest.approx(0.5, abs=1e-6)
    assert fair[1] == pytest.approx(0.5, abs=1e-6)


def test_devig_methods_both_sum_to_one_on_asymmetric_market():
    mult = multiplicative_devig([1.50, 2.75])
    pw = power_devig([1.50, 2.75])
    assert sum(mult) == pytest.approx(1.0, abs=1e-9)
    assert sum(pw) == pytest.approx(1.0, abs=1e-9)


def test_power_devig_three_way_market():
    # a real UCL "Match Winner" market (Real Madrid vs Inter, 2026-09-07)
    fair = power_devig([1.61, 4.55, 4.35])
    assert sum(fair) == pytest.approx(1.0, abs=1e-9)
    assert fair[0] > fair[1]  # Real Madrid (home) more likely than a draw
    assert fair[0] > fair[2]  # and more likely than Inter winning


def test_calculate_clv_pct_positive_when_beating_close():
    # took 1.95, fair closing price works out lower -> positive CLV
    clv = calculate_clv_pct(1.95, [1.87, 2.02], selection_index=0, method="power")
    assert clv > 0


def test_bet_logger_full_lifecycle(tmp_path):
    logger = BetLogger(db_path=":memory:")
    bet_id = logger.log_bet(sport="NBA", game_id="g1", market="total", selection="Over 54.5", stake=1.0, odds_taken=1.95)
    clv = logger.record_closing_line(bet_id, closing_odds_market=[1.87, 2.02], selection_index=0, method="power")
    logger.record_outcome(bet_id, "win")

    summary = logger.summary()
    assert summary["total_bets"] == 1
    assert summary["settled_bets"] == 1
    assert summary["win_rate"] == pytest.approx(1.0)
    assert summary["avg_clv_pct"] == pytest.approx(clv)
    assert summary["net_units"] == pytest.approx(0.95)


def test_bet_logger_loss_subtracts_stake():
    logger = BetLogger(db_path=":memory:")
    bet_id = logger.log_bet(sport="NBA", game_id="g2", market="moneyline", selection="Away", stake=2.0, odds_taken=1.80)
    logger.record_outcome(bet_id, "loss")
    assert logger.summary()["net_units"] == pytest.approx(-2.0)


def test_summary_by_rule_groups_bets_by_trigger_rule():
    logger = BetLogger(db_path=":memory:")

    bet1 = logger.log_bet(sport="NBA", game_id="g1", market="total", selection="Over", stake=1.0, odds_taken=2.0, trigger_rule="bonus_trigger")
    logger.record_outcome(bet1, "win")

    bet2 = logger.log_bet(sport="NBA", game_id="g2", market="total", selection="Over", stake=1.0, odds_taken=2.0, trigger_rule="bonus_trigger")
    logger.record_outcome(bet2, "loss")

    bet3 = logger.log_bet(sport="EPL", game_id="g3", market="moneyline", selection="Home", stake=1.0, odds_taken=3.0, trigger_rule="red_card_state_shift")
    logger.record_outcome(bet3, "win")

    by_rule = logger.summary_by_rule()

    assert set(by_rule.keys()) == {"bonus_trigger", "red_card_state_shift"}
    assert by_rule["bonus_trigger"]["total_bets"] == 2
    assert by_rule["bonus_trigger"]["win_rate"] == pytest.approx(0.5)
    assert by_rule["red_card_state_shift"]["total_bets"] == 1
    assert by_rule["red_card_state_shift"]["win_rate"] == pytest.approx(1.0)


def test_summary_by_rule_groups_untagged_bets_separately():
    logger = BetLogger(db_path=":memory:")
    logger.log_bet(sport="NBA", game_id="g1", market="total", selection="Over", stake=1.0, odds_taken=2.0)  # no trigger_rule

    by_rule = logger.summary_by_rule()

    assert "untagged" in by_rule
    assert by_rule["untagged"]["total_bets"] == 1


def test_bet_logger_migrates_older_db_missing_trigger_rule_column(tmp_path):
    db_path = str(tmp_path / "old_bets.db")

    # simulate a bets.db created before trigger_rule existed
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE bets (
            bet_id TEXT PRIMARY KEY,
            sport TEXT,
            game_id TEXT,
            market TEXT,
            selection TEXT,
            stake REAL,
            odds_taken REAL,
            placed_at REAL,
            closing_odds_market TEXT,
            selection_index INTEGER,
            outcome TEXT,
            clv_pct REAL
        )
        """
    )
    conn.execute(
        "INSERT INTO bets (bet_id, sport, game_id, market, selection, stake, odds_taken, placed_at) "
        "VALUES ('old-1', 'NBA', 'g1', 'total', 'Over', 1.0, 2.0, 0)"
    )
    conn.commit()
    conn.close()

    # opening it through BetLogger should migrate the schema, not crash
    logger = BetLogger(db_path=db_path)
    bet_id = logger.log_bet(sport="NBA", game_id="g2", market="total", selection="Over", stake=1.0, odds_taken=2.0, trigger_rule="bonus_trigger")

    by_rule = logger.summary_by_rule()
    assert by_rule["untagged"]["total_bets"] == 1  # the pre-migration row
    assert by_rule["bonus_trigger"]["total_bets"] == 1  # the new row
    assert logger.summary()["total_bets"] == 2


def test_bootstrap_ci_returns_none_below_min_samples():
    assert bootstrap_ci([1.0, 2.0, 3.0]) is None
    assert len([1.0, 2.0, 3.0]) < MIN_SAMPLES_FOR_CI  # sanity-check the fixture matches the constant


def test_bootstrap_ci_contains_the_true_mean_for_tight_data():
    values = [5.0] * 20  # zero variance - CI should collapse to (5.0, 5.0)
    ci = bootstrap_ci(values)
    assert ci is not None
    assert ci[0] == pytest.approx(5.0, abs=1e-9)
    assert ci[1] == pytest.approx(5.0, abs=1e-9)


def test_bootstrap_ci_bounds_are_ordered_and_bracket_sample_mean():
    values = [-3.0, -1.0, 0.5, 1.0, 2.0, 4.0, -2.0, 3.0, 1.5, -0.5]
    ci = bootstrap_ci(values)
    assert ci is not None
    lo, hi = ci
    assert lo <= hi
    sample_mean = sum(values) / len(values)
    # bootstrap CI on the mean should bracket the sample mean itself
    assert lo <= sample_mean <= hi


def test_summary_reports_ci_and_sample_size():
    logger = BetLogger(db_path=":memory:")
    for i in range(10):
        bet_id = logger.log_bet(sport="NBA", game_id=f"g{i}", market="total", selection="Over", stake=1.0, odds_taken=2.0)
        logger.record_closing_line(bet_id, closing_odds_market=[1.9, 1.9], selection_index=0)
        logger.record_outcome(bet_id, "win")

    summary = logger.summary()
    assert summary["avg_clv_n"] == 10
    assert summary["avg_clv_ci95"] is not None


def test_summary_reports_avg_odds_taken_from_settled_bets_only():
    logger = BetLogger(db_path=":memory:")
    bet1 = logger.log_bet(sport="NBA", game_id="g1", market="total", selection="Over", stake=1.0, odds_taken=2.0)
    logger.record_outcome(bet1, "win")
    bet2 = logger.log_bet(sport="NBA", game_id="g2", market="total", selection="Over", stake=1.0, odds_taken=4.0)
    logger.record_outcome(bet2, "loss")
    logger.log_bet(sport="NBA", game_id="g3", market="total", selection="Over", stake=1.0, odds_taken=100.0)  # unsettled - excluded

    assert logger.summary()["avg_odds_taken"] == pytest.approx(3.0)


def test_summary_omits_ci_below_min_samples():
    logger = BetLogger(db_path=":memory:")
    bet_id = logger.log_bet(sport="NBA", game_id="g1", market="total", selection="Over", stake=1.0, odds_taken=2.0)
    logger.record_closing_line(bet_id, closing_odds_market=[1.9, 1.9], selection_index=0)

    summary = logger.summary()
    assert summary["avg_clv_n"] == 1
    assert summary["avg_clv_ci95"] is None
