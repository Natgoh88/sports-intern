"""
trigger_classifier.py

A thin scoring layer on top of the rule engine: rules still generate
every candidate trigger (interpretable, auditable, unchanged), but once
enough real logged-and-settled bets exist, a small logistic regression
trained on trigger_rule + odds_taken can score how much better or
worse than the market-implied probability that rule's bets actually
win - turning "fires or doesn't" into "fires with a confidence score."

WHY LOGISTIC REGRESSION, NOT SOMETHING FANCIER
------------------------------------------------
At the sample sizes this app will realistically produce (dozens to a
few hundred settled bets, not millions), a more expressive model
doesn't have enough data to justify its extra variance - it would
overfit to noise dressed up as pattern. Logistic regression's few
parameters and interpretable coefficients are the honest choice here,
not a compromise.

WHAT THIS DOES NOT PRETEND TO DO
------------------------------------------------
This module ships with zero real bets logged. train() refuses to
produce a model below MIN_TRAINING_SAMPLES and says so explicitly,
rather than silently training (and confidently mis-scoring) on a
handful of rows. Until real data accumulates, score() always returns
None and callers should fall back to the raw rule signal - a
classifier with no evidence behind it is worse than no classifier.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import OneHotEncoder

MIN_TRAINING_SAMPLES = 20


@dataclass
class TrainedModel:
    model: LogisticRegression
    encoder: OneHotEncoder
    rule_names: list[str]
    n_samples: int
    train_accuracy: float


def _bets_to_rows(db_path: str) -> list[tuple]:
    """Returns (trigger_rule, odds_taken, won) for every settled,
    non-push bet - the only rows a classifier can learn from."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT trigger_rule, odds_taken, outcome FROM bets WHERE outcome IN ('win', 'loss')"
    ).fetchall()
    conn.close()
    return [(rule or "untagged", odds, 1 if outcome == "win" else 0) for rule, odds, outcome in rows]


def train(db_path: str = "bets.db") -> Optional[TrainedModel]:
    """Trains on real settled bets in bets.db. Returns None (not a
    degenerate model) below MIN_TRAINING_SAMPLES."""
    if not os.path.exists(db_path):
        return None

    rows = _bets_to_rows(db_path)
    if len(rows) < MIN_TRAINING_SAMPLES:
        return None

    rule_names = sorted({r[0] for r in rows})
    return _train_from_rows(rows, rule_names)


def _train_from_rows(rows: list[tuple], rule_names: list[str]) -> TrainedModel:
    encoder = OneHotEncoder(categories=[rule_names], handle_unknown="ignore", sparse_output=False)
    rule_col = [[r[0]] for r in rows]
    rule_encoded = encoder.fit_transform(rule_col)

    implied_prob = [[1.0 / r[1]] for r in rows]
    X = [list(enc) + prob for enc, prob in zip(rule_encoded, implied_prob)]
    y = [r[2] for r in rows]

    model = LogisticRegression(max_iter=1000)
    model.fit(X, y)
    accuracy = model.score(X, y)

    return TrainedModel(model=model, encoder=encoder, rule_names=rule_names, n_samples=len(rows), train_accuracy=accuracy)


def score(trained: Optional[TrainedModel], trigger_rule: str, odds_taken: float) -> Optional[float]:
    """Predicted win probability for a hypothetical bet on this rule at
    these odds. Returns None if no model has enough real data behind
    it yet - callers should fall back to the raw rule signal, not to a
    guess dressed up as a number."""
    if trained is None:
        return None
    rule_encoded = trained.encoder.transform([[trigger_rule]])
    X = [list(rule_encoded[0]) + [1.0 / odds_taken]]
    return float(trained.model.predict_proba(X)[0][1])


if __name__ == "__main__":
    # demo on synthetic data - mirrors this project's convention of a
    # runnable mock-data demo in every core module. bonus_trigger wins
    # more than its market-implied odds suggest; a "noise_trigger" is
    # exactly market-efficient (no edge) - the model should learn to
    # tell them apart.
    import random

    random.seed(7)
    synthetic_rows = []
    for _ in range(200):
        if random.random() < 0.5:
            rule, odds, true_win_prob = "bonus_trigger", 2.0, 0.58  # real edge vs implied 50%
        else:
            rule, odds, true_win_prob = "noise_trigger", 2.0, 0.50  # no edge
        won = 1 if random.random() < true_win_prob else 0
        synthetic_rows.append((rule, odds, won))

    trained = _train_from_rows(synthetic_rows, ["bonus_trigger", "noise_trigger"])
    print(f"Trained on {trained.n_samples} synthetic samples, train accuracy {trained.train_accuracy:.1%}")
    print(f"P(win) for bonus_trigger @ 2.0:  {score(trained, 'bonus_trigger', 2.0):.1%}  (true edge: 58%)")
    print(f"P(win) for noise_trigger @ 2.0:  {score(trained, 'noise_trigger', 2.0):.1%}  (true edge: 50%)")
    print()
    print("Against real bets.db:", "not enough data yet" if train() is None else "model trained")
