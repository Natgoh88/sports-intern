import random

from trigger_classifier import train, score, _train_from_rows, MIN_TRAINING_SAMPLES


def test_train_returns_none_for_missing_db(tmp_path):
    assert train(db_path=str(tmp_path / "nonexistent.db")) is None


def test_train_returns_none_below_min_samples(tmp_path):
    import sqlite3

    db_path = str(tmp_path / "bets.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE bets (trigger_rule TEXT, odds_taken REAL, outcome TEXT)")
    for i in range(MIN_TRAINING_SAMPLES - 1):
        conn.execute("INSERT INTO bets VALUES ('bonus_trigger', 2.0, 'win')")
    conn.commit()
    conn.close()

    assert train(db_path=db_path) is None


def test_train_succeeds_at_min_samples(tmp_path):
    import sqlite3

    db_path = str(tmp_path / "bets.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE bets (trigger_rule TEXT, odds_taken REAL, outcome TEXT)")
    for i in range(MIN_TRAINING_SAMPLES):
        outcome = "win" if i % 2 == 0 else "loss"
        conn.execute("INSERT INTO bets VALUES ('bonus_trigger', 2.0, ?)", (outcome,))
    conn.commit()
    conn.close()

    trained = train(db_path=db_path)
    assert trained is not None
    assert trained.n_samples == MIN_TRAINING_SAMPLES


def test_score_returns_none_without_a_trained_model():
    assert score(None, "bonus_trigger", 2.0) is None


def test_score_returns_a_probability_between_zero_and_one():
    random.seed(1)
    rows = [("bonus_trigger", 2.0, 1 if random.random() < 0.6 else 0) for _ in range(50)]
    trained = _train_from_rows(rows, ["bonus_trigger"])

    p = score(trained, "bonus_trigger", 2.0)

    assert p is not None
    assert 0.0 <= p <= 1.0


def test_classifier_distinguishes_a_real_edge_from_no_edge():
    random.seed(7)
    rows = []
    for _ in range(300):
        if random.random() < 0.5:
            rule, true_win_prob = "edge_trigger", 0.65
        else:
            rule, true_win_prob = "noise_trigger", 0.50
        won = 1 if random.random() < true_win_prob else 0
        rows.append((rule, 2.0, won))

    trained = _train_from_rows(rows, ["edge_trigger", "noise_trigger"])

    edge_score = score(trained, "edge_trigger", 2.0)
    noise_score = score(trained, "noise_trigger", 2.0)

    assert edge_score > noise_score


def test_score_handles_an_unseen_rule_gracefully():
    rows = [("bonus_trigger", 2.0, i % 2) for i in range(30)]
    trained = _train_from_rows(rows, ["bonus_trigger"])

    # a rule never seen during training - handle_unknown="ignore" should
    # let this through as an all-zero encoding rather than raising
    p = score(trained, "never_seen_before_trigger", 2.0)
    assert p is not None
    assert 0.0 <= p <= 1.0
