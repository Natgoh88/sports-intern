"""
bankroll_sim.py

Monte Carlo bankroll simulator using fractional-Kelly staking. Turns
"this rule's average CLV is +2%" into "given this edge, here's the
actual range of bankroll outcomes over N future bets" - a measured
edge alone tells you nothing about position sizing or realistic
variance around it.

Kelly's classic formula answers "what fraction of bankroll maximizes
long-run growth rate," not "what should I actually bet." Full Kelly is
behaviorally brutal - its own textbook drawdowns are severe even when
the edge is real and durable - so practitioners almost always bet a
fraction of it (kelly_fraction_multiplier, e.g. 0.5 for "half Kelly")
to trade some growth rate for a lot less variance. This defaults to
0.5 for that reason, not because full Kelly is wrong, but because most
people can't stomach what it actually asks of them.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


def kelly_fraction(win_prob: float, decimal_odds: float) -> float:
    """f* = (b*p - q) / b, where b is net odds (decimal_odds - 1).
    Returns 0 if there's no edge (f* would be negative) - Kelly never
    tells you to bet against your own edge, it tells you not to bet."""
    b = decimal_odds - 1
    if b <= 0:
        return 0.0
    p = win_prob
    q = 1 - p
    f_star = (b * p - q) / b
    return max(0.0, f_star)


@dataclass
class SimulationResult:
    starting_bankroll: float
    n_bets: int
    n_simulations: int
    kelly_fraction_used: float
    final_bankrolls: list[float]
    prob_of_ruin: float  # fraction of runs ending below ruin_threshold * starting_bankroll

    def percentile(self, pct: float) -> float:
        s = sorted(self.final_bankrolls)
        idx = min(len(s) - 1, max(0, int(pct / 100 * len(s))))
        return s[idx]


def simulate(
    win_prob: float,
    decimal_odds: float,
    starting_bankroll: float = 100.0,
    n_bets: int = 100,
    n_simulations: int = 2000,
    kelly_fraction_multiplier: float = 0.5,
    ruin_threshold: float = 0.2,
) -> SimulationResult:
    """Runs n_simulations independent paths of n_bets each, staking
    kelly_fraction_multiplier * full-Kelly of the *current* (compounding)
    bankroll on every bet at the same win_prob/decimal_odds. A path
    counts as "ruined" if it ends below ruin_threshold of the starting
    bankroll, not just literally at zero - down 80% is a real bankroll-
    ending outcome for most people well before it hits exactly 0."""
    f = kelly_fraction(win_prob, decimal_odds) * kelly_fraction_multiplier
    final_bankrolls = []
    ruin_count = 0

    for _ in range(n_simulations):
        bankroll = starting_bankroll
        for _ in range(n_bets):
            if bankroll <= 0:
                break
            stake = bankroll * f
            if random.random() < win_prob:
                bankroll += stake * (decimal_odds - 1)
            else:
                bankroll -= stake
        final_bankrolls.append(bankroll)
        if bankroll < ruin_threshold * starting_bankroll:
            ruin_count += 1

    return SimulationResult(
        starting_bankroll=starting_bankroll,
        n_bets=n_bets,
        n_simulations=n_simulations,
        kelly_fraction_used=f,
        final_bankrolls=final_bankrolls,
        prob_of_ruin=ruin_count / n_simulations,
    )


if __name__ == "__main__":
    # demo: a modest but real edge (52% to win at even-ish odds) over
    # 200 bets, half-Kelly staking
    result = simulate(win_prob=0.56, decimal_odds=1.91, n_bets=200, n_simulations=5000)
    print(f"Kelly fraction used (half-Kelly): {result.kelly_fraction_used:.1%} of bankroll per bet")
    print(f"Median final bankroll: {result.percentile(50):.2f} (started at {result.starting_bankroll:.2f})")
    print(f"5th percentile: {result.percentile(5):.2f}   95th percentile: {result.percentile(95):.2f}")
    print(f"P(ruin, i.e. ending below 20% of starting bankroll): {result.prob_of_ruin:.1%}")
