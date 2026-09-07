import json

from trigger_stats import load_trigger_rows, frequency_by_rule


def _write_jsonl(path, rows):
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def test_load_trigger_rows_missing_file_returns_empty():
    assert load_trigger_rows("/nonexistent/path/triggers.log.jsonl") == []


def test_load_trigger_rows_parses_jsonl(tmp_path):
    path = tmp_path / "triggers.log.jsonl"
    _write_jsonl(path, [{"rule_name": "bonus_trigger", "game_id": "g1", "fired_at": 1.0}])

    rows = load_trigger_rows(str(path))

    assert len(rows) == 1
    assert rows[0]["rule_name"] == "bonus_trigger"


def test_load_trigger_rows_skips_blank_lines(tmp_path):
    path = tmp_path / "triggers.log.jsonl"
    path.write_text('{"rule_name": "a", "game_id": "g1", "fired_at": 1.0}\n\n\n')

    assert len(load_trigger_rows(str(path))) == 1


def test_frequency_by_rule_counts_and_distinct_games():
    rows = [
        {"rule_name": "bonus_trigger", "game_id": "g1", "fired_at": 1.0},
        {"rule_name": "bonus_trigger", "game_id": "g1", "fired_at": 2.0},
        {"rule_name": "bonus_trigger", "game_id": "g2", "fired_at": 3.0},
        {"rule_name": "red_card_state_shift", "game_id": "g3", "fired_at": 1.5},
    ]

    stats = frequency_by_rule(rows)

    assert stats["bonus_trigger"]["count"] == 3
    assert stats["bonus_trigger"]["distinct_games"] == 2
    assert stats["bonus_trigger"]["last_fired_at"] == 3.0
    assert stats["red_card_state_shift"]["count"] == 1
    assert stats["red_card_state_shift"]["distinct_games"] == 1


def test_frequency_by_rule_empty_input_returns_empty_dict():
    assert frequency_by_rule([]) == {}
