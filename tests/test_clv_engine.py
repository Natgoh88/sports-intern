import pytest

from clv_engine import (
    implied_prob,
    multiplicative_devig,
    power_devig,
    calculate_clv_pct,
    BetLogger,
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
