import json

import pytest
from fastapi.testclient import TestClient

import api


@pytest.fixture
def client(tmp_path, monkeypatch):
    trigger_log = tmp_path / "triggers.log.jsonl"
    bets_db = tmp_path / "bets.db"
    monkeypatch.setattr(api, "TRIGGER_LOG_PATH", str(trigger_log))
    monkeypatch.setattr(api, "BETS_DB_PATH", str(bets_db))
    return TestClient(api.app), trigger_log, bets_db


def test_health_returns_ok(client):
    test_client, _, _ = client
    resp = test_client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_triggers_empty_when_no_log(client):
    test_client, _, _ = client
    resp = test_client.get("/triggers")
    assert resp.status_code == 200
    assert resp.json() == []


def test_triggers_returns_rows_newest_first(client):
    test_client, trigger_log, _ = client
    rows = [
        {"game_id": "g1", "rule_name": "bonus_trigger", "message": "a", "market_hint": "n/a", "fired_at": 1.0},
        {"game_id": "g2", "rule_name": "bonus_trigger", "message": "b", "market_hint": "n/a", "fired_at": 5.0},
    ]
    trigger_log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    resp = test_client.get("/triggers")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["game_id"] == "g2"  # newest (fired_at=5.0) first


def test_triggers_respects_limit(client):
    test_client, trigger_log, _ = client
    rows = [{"game_id": f"g{i}", "rule_name": "bonus_trigger", "message": "x", "market_hint": "n/a", "fired_at": float(i)} for i in range(10)]
    trigger_log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    resp = test_client.get("/triggers", params={"limit": 3})

    assert len(resp.json()) == 3


def test_triggers_frequency_empty_when_no_log(client):
    test_client, _, _ = client
    resp = test_client.get("/triggers/frequency")
    assert resp.status_code == 200
    assert resp.json() == {}


def test_triggers_frequency_counts_by_rule(client):
    test_client, trigger_log, _ = client
    rows = [
        {"game_id": "g1", "rule_name": "bonus_trigger", "fired_at": 1.0},
        {"game_id": "g1", "rule_name": "bonus_trigger", "fired_at": 2.0},
        {"game_id": "g2", "rule_name": "red_card_state_shift", "fired_at": 3.0},
    ]
    trigger_log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    resp = test_client.get("/triggers/frequency")

    data = resp.json()
    assert data["bonus_trigger"]["count"] == 2
    assert data["red_card_state_shift"]["count"] == 1


def test_bets_summary_returns_empty_shape_when_no_db(client):
    test_client, _, _ = client
    resp = test_client.get("/bets/summary")
    assert resp.status_code == 200
    assert resp.json()["total_bets"] == 0


def test_bets_summary_by_rule_returns_empty_dict_when_no_db(client):
    test_client, _, _ = client
    resp = test_client.get("/bets/summary-by-rule")
    assert resp.status_code == 200
    assert resp.json() == {}


def test_bets_summary_reflects_real_logged_bet(client):
    test_client, _, bets_db = client
    from clv_engine import BetLogger

    logger = BetLogger(db_path=str(bets_db))
    bet_id = logger.log_bet(sport="NBA", game_id="g1", market="total", selection="Over", stake=1.0, odds_taken=2.0, trigger_rule="bonus_trigger")
    logger.record_outcome(bet_id, "win")

    resp = test_client.get("/bets/summary")

    assert resp.json()["total_bets"] == 1
    assert resp.json()["net_units"] == pytest.approx(1.0)


def test_metrics_is_plain_text_and_includes_scanner_status(client, monkeypatch):
    test_client, _, _ = client
    monkeypatch.setattr(api.scanner_manager, "status", lambda name: {"running": False, "pid": None})
    monkeypatch.setattr(api.scanner_manager, "heartbeat_age_seconds", lambda name: None)

    resp = test_client.get("/metrics")

    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    assert 'scanner_up{name="soccer"} 0' in resp.text


def test_metrics_reflects_running_scanner(client, monkeypatch):
    test_client, _, _ = client

    def fake_status(name):
        return {"running": True, "pid": 123} if name == "soccer" else {"running": False, "pid": None}

    monkeypatch.setattr(api.scanner_manager, "status", fake_status)
    monkeypatch.setattr(api.scanner_manager, "heartbeat_age_seconds", lambda name: 12.5)

    resp = test_client.get("/metrics")

    assert 'scanner_up{name="soccer"} 1' in resp.text
    assert 'scanner_heartbeat_age_seconds{name="soccer"} 12.5' in resp.text
