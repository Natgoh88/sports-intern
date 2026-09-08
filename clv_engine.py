"""
clv_engine.py

Bet logger + de-vig + closing line value (CLV) calculator.

De-vig methods:
- multiplicative: proportional removal of the overround. Fast, fine
  for tight two-way markets, gets biased on lines with a big favorite.
- power: solves for a single exponent k such that
  sum((1/odds_i)^k) == 1, then fair_prob_i = (1/odds_i)^k. Tends to
  track real market probabilities better than multiplicative once the
  market's lopsided (heavy favorite / big underdog).

CLV is computed against a de-vigged closing line, not the raw closing
odds, so you're comparing fair probability to fair probability instead
of your price against a number that still has the book's edge baked
into it.
"""

from __future__ import annotations

import json
import random
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Optional

# Below this many CLV samples, a confidence interval is wide enough to
# be meaningless - flagged separately from the interval itself so the
# UI can say "not enough data yet" instead of showing a technically-
# correct but useless [-340%, +290%] range.
MIN_SAMPLES_FOR_CI = 8


def bootstrap_ci(values: list[float], confidence: float = 0.95, n_resamples: int = 2000) -> Optional[tuple[float, float]]:
    """Percentile bootstrap confidence interval on the mean of `values`.
    Used instead of a normal-approximation (mean +/- 1.96*SE) interval
    because CLV distributions are routinely skewed (a handful of big
    favorite/underdog lines dominate the tail) and bootstrap doesn't
    assume normality. Returns None below MIN_SAMPLES_FOR_CI - resampling
    3 numbers 2000 times doesn't manufacture information that isn't
    there, it just produces a confident-looking number from noise.
    """
    if len(values) < MIN_SAMPLES_FOR_CI:
        return None

    means = []
    n = len(values)
    for _ in range(n_resamples):
        resample = random.choices(values, k=n)
        means.append(sum(resample) / n)
    means.sort()

    alpha = 1 - confidence
    lo_idx = int((alpha / 2) * n_resamples)
    hi_idx = int((1 - alpha / 2) * n_resamples) - 1
    return means[lo_idx], means[hi_idx]

# ---------------------------------------------------------------------------
# De-vig
# ---------------------------------------------------------------------------

def implied_prob(decimal_odds: float) -> float:
    return 1.0 / decimal_odds


def multiplicative_devig(decimal_odds: list[float]) -> list[float]:
    """Scales raw implied probabilities down proportionally so they sum to 1."""
    raw = [implied_prob(o) for o in decimal_odds]
    overround = sum(raw)
    return [p / overround for p in raw]


def power_devig(decimal_odds: list[float], tolerance: float = 1e-10, max_iter: int = 100) -> list[float]:
    """
    Solves for exponent k such that sum((1/odds_i)^k) == 1 via
    bisection, then returns fair_prob_i = (1/odds_i)^k for each
    outcome. Since every raw implied probability is < 1, raising to a
    higher power always shrinks it - so sum(p_i^k) is monotonically
    decreasing in k, which is what makes the bisection well-behaved.
    Overround means sum(p_i^1) > 1, so the root sits at some k > 1.
    """
    raw = [implied_prob(o) for o in decimal_odds]

    def total_prob(k: float) -> float:
        return sum(p ** k for p in raw)

    if total_prob(1.0) <= 1.0:
        raise ValueError("Odds imply no overround (or an arbitrage) - check inputs")

    lo, hi = 1.0, 2.0
    while total_prob(hi) > 1.0:
        hi *= 2
        if hi > 1000:
            raise RuntimeError("power de-vig failed to converge - check inputs")

    k = 1.0
    for _ in range(max_iter):
        k = (lo + hi) / 2
        t = total_prob(k)
        if abs(t - 1.0) < tolerance:
            break
        if t > 1.0:
            lo = k
        else:
            hi = k

    return [p ** k for p in raw]


def fair_odds_from_prob(prob: float) -> float:
    return 1.0 / prob


# ---------------------------------------------------------------------------
# CLV calculation
# ---------------------------------------------------------------------------

def calculate_clv_pct(odds_taken: float, closing_odds_market: list[float], selection_index: int, method: str = "power") -> float:
    """
    Returns CLV as a percentage. Positive means you beat the closing
    line - you got a better price than the fair probability at close
    implied you should have.

    closing_odds_market: decimal odds for every outcome in the market
    at closing (needed to de-vig properly - you can't de-vig one side
    alone).
    selection_index: index into that list for the side you actually bet.
    """
    devig_fn = power_devig if method == "power" else multiplicative_devig
    fair_probs = devig_fn(closing_odds_market)
    fair_closing_odds = fair_odds_from_prob(fair_probs[selection_index])
    return (odds_taken - fair_closing_odds) / fair_closing_odds * 100.0


# ---------------------------------------------------------------------------
# Bet logger
# ---------------------------------------------------------------------------

@dataclass
class Bet:
    bet_id: str
    sport: str
    game_id: str
    market: str  # e.g. "spread", "total", "moneyline", "corners_over_9.5"
    selection: str  # e.g. "PHI -3.5"
    stake: float
    odds_taken: float  # decimal odds
    placed_at: float
    outcome: Optional[str] = None  # "win" / "loss" / "push" / None
    clv_pct: Optional[float] = None
    trigger_rule: Optional[str] = None  # e.g. "bonus_trigger" - which rule's alert led to this bet


class BetLogger:
    def __init__(self, db_path: str = "bets.db"):
        self.conn = sqlite3.connect(db_path)
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bets (
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
                clv_pct REAL,
                trigger_rule TEXT
            )
            """
        )
        # migration for a bets.db created before trigger_rule existed -
        # CREATE TABLE IF NOT EXISTS above only covers a fresh database
        existing_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(bets)")}
        if "trigger_rule" not in existing_columns:
            self.conn.execute("ALTER TABLE bets ADD COLUMN trigger_rule TEXT")
        self.conn.commit()

    def log_bet(
        self,
        sport: str,
        game_id: str,
        market: str,
        selection: str,
        stake: float,
        odds_taken: float,
        trigger_rule: Optional[str] = None,
    ) -> str:
        bet_id = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO bets (bet_id, sport, game_id, market, selection, stake, odds_taken, placed_at, trigger_rule) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (bet_id, sport, game_id, market, selection, stake, odds_taken, time.time(), trigger_rule),
        )
        self.conn.commit()
        return bet_id

    def record_closing_line(self, bet_id: str, closing_odds_market: list[float], selection_index: int, method: str = "power") -> float:
        row = self.conn.execute("SELECT odds_taken FROM bets WHERE bet_id = ?", (bet_id,)).fetchone()
        if row is None:
            raise ValueError(f"No bet with id {bet_id}")

        clv_pct = calculate_clv_pct(row[0], closing_odds_market, selection_index, method=method)
        self.conn.execute(
            "UPDATE bets SET closing_odds_market = ?, selection_index = ?, clv_pct = ? WHERE bet_id = ?",
            (json.dumps(closing_odds_market), selection_index, clv_pct, bet_id),
        )
        self.conn.commit()
        return clv_pct

    def record_outcome(self, bet_id: str, outcome: str) -> None:
        assert outcome in ("win", "loss", "push")
        self.conn.execute("UPDATE bets SET outcome = ? WHERE bet_id = ?", (outcome, bet_id))
        self.conn.commit()

    def list_open_bets(self) -> list[dict]:
        """Bets with no recorded outcome yet - what a UI needs to show
        'close this out' actions for, instead of requiring the caller
        to already know a bet_id (which is exactly what made log_bet.py
        CLI-only and not dashboard-friendly)."""
        columns = [
            "bet_id", "sport", "game_id", "market", "selection", "stake",
            "odds_taken", "placed_at", "closing_odds_market", "trigger_rule",
        ]
        rows = self.conn.execute(
            f"SELECT {', '.join(columns)} FROM bets WHERE outcome IS NULL ORDER BY placed_at DESC"
        ).fetchall()
        return [dict(zip(columns, row)) for row in rows]

    @staticmethod
    def _summarize_rows(rows: list[tuple]) -> dict:
        """rows: (stake, odds_taken, outcome, clv_pct) tuples."""
        settled = [r for r in rows if r[2] is not None]
        wins = [r for r in settled if r[2] == "win"]
        clv_values = [r[3] for r in rows if r[3] is not None]

        units = 0.0
        for stake, odds, outcome, _ in rows:
            if outcome == "win":
                units += stake * (odds - 1)
            elif outcome == "loss":
                units -= stake
            # push contributes 0

        ci = bootstrap_ci(clv_values)
        settled_odds = [r[1] for r in settled]

        return {
            "total_bets": len(rows),
            "settled_bets": len(settled),
            "win_rate": (len(wins) / len(settled)) if settled else None,
            "avg_odds_taken": (sum(settled_odds) / len(settled_odds)) if settled_odds else None,
            "avg_clv_pct": (sum(clv_values) / len(clv_values)) if clv_values else None,
            "avg_clv_ci95": ci,  # (low, high) or None if under MIN_SAMPLES_FOR_CI
            "avg_clv_n": len(clv_values),
            "net_units": units,
        }

    def summary(self) -> dict:
        rows = self.conn.execute("SELECT stake, odds_taken, outcome, clv_pct FROM bets").fetchall()
        return self._summarize_rows(rows)

    def summary_by_rule(self) -> dict[str, dict]:
        """Same stats as summary(), grouped by the trigger_rule that led
        to each bet. This is what actually answers 'is this rule worth
        anything' - an aggregate CLV number can hide a great rule and a
        dead one averaging out to mediocre. Bets logged without a
        trigger_rule (e.g. via the pre-linkage CLI, or a manual bet not
        tied to an alert) are grouped under 'untagged'."""
        rows = self.conn.execute("SELECT stake, odds_taken, outcome, clv_pct, trigger_rule FROM bets").fetchall()
        by_rule: dict[str, list[tuple]] = {}
        for stake, odds, outcome, clv, rule in rows:
            by_rule.setdefault(rule or "untagged", []).append((stake, odds, outcome, clv))
        return {rule: self._summarize_rows(bet_rows) for rule, bet_rows in by_rule.items()}


if __name__ == "__main__":
    # de-vig sanity check: symmetric -110/-110 market should land at 50/50
    fair = power_devig([1.909, 1.909])
    assert abs(fair[0] - 0.5) < 1e-6 and abs(fair[1] - 0.5) < 1e-6, fair

    # asymmetric market: power and multiplicative should both sum to 1
    # but disagree slightly on the split
    mult = multiplicative_devig([1.50, 2.75])
    pw = power_devig([1.50, 2.75])
    assert abs(sum(mult) - 1.0) < 1e-9
    assert abs(sum(pw) - 1.0) < 1e-9
    print(f"multiplicative fair probs: {mult}")
    print(f"power fair probs:          {pw}")

    logger = BetLogger(db_path=":memory:")
    bet_id = logger.log_bet(sport="NBA", game_id="demo-1", market="total", selection="Q1 Over 54.5", stake=1.0, odds_taken=1.95)
    clv = logger.record_closing_line(bet_id, closing_odds_market=[1.87, 2.02], selection_index=0, method="power")
    logger.record_outcome(bet_id, "win")
    print(f"CLV on that bet: {clv:.2f}%")
    print("summary:", logger.summary())
