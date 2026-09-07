import pytest

from bankroll_sim import kelly_fraction, simulate


def test_kelly_fraction_zero_edge_is_zero():
    # fair coin at fair (2.0) odds - no edge either way
    assert kelly_fraction(win_prob=0.5, decimal_odds=2.0) == pytest.approx(0.0)


def test_kelly_fraction_positive_edge():
    # 60% win chance at even-money odds - real edge
    f = kelly_fraction(win_prob=0.6, decimal_odds=2.0)
    assert f == pytest.approx(0.2, abs=1e-9)


def test_kelly_fraction_negative_edge_clamped_to_zero():
    # 40% win chance at even-money odds - negative edge, never bet
    assert kelly_fraction(win_prob=0.4, decimal_odds=2.0) == 0.0


def test_kelly_fraction_handles_odds_at_or_below_evens():
    assert kelly_fraction(win_prob=0.9, decimal_odds=1.0) == 0.0


def test_simulate_zero_edge_never_stakes_bankroll_unchanged():
    result = simulate(win_prob=0.5, decimal_odds=2.0, starting_bankroll=100.0, n_bets=50, n_simulations=20)
    assert result.kelly_fraction_used == pytest.approx(0.0)
    assert all(b == pytest.approx(100.0) for b in result.final_bankrolls)
    assert result.prob_of_ruin == 0.0


def test_simulate_strong_edge_grows_bankroll_on_average():
    result = simulate(win_prob=0.65, decimal_odds=2.0, starting_bankroll=100.0, n_bets=100, n_simulations=500)
    assert result.percentile(50) > result.starting_bankroll


def test_simulate_prob_of_ruin_is_a_valid_probability():
    result = simulate(win_prob=0.55, decimal_odds=1.9, n_bets=100, n_simulations=200)
    assert 0.0 <= result.prob_of_ruin <= 1.0


def test_percentile_ordering_is_monotonic():
    result = simulate(win_prob=0.55, decimal_odds=2.0, n_bets=50, n_simulations=500)
    assert result.percentile(5) <= result.percentile(50) <= result.percentile(95)
