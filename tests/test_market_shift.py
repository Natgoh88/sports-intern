import run_soccer
from soccer_scanner import TriggerEvent


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def raise_for_status(self):
        pass

    async def json(self):
        return self._payload


class _FakeSession:
    """Returns a fixed odds payload regardless of which fixture/params
    are requested - enough to test on_trigger's metadata enrichment
    without a real network call."""

    def __init__(self, odds_payload):
        self._odds_payload = odds_payload

    def get(self, url, params=None):
        return _FakeResponse(self._odds_payload)


def _odds_payload(home_odd, draw_odd, away_odd):
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


async def test_fetch_win_probs_from_odds_labels_sides_correctly():
    session = _FakeSession(_odds_payload(1.5, 4.5, 6.0))

    probs = await run_soccer.fetch_win_probs_from_odds(session, "123", "Man City", "Bournemouth")

    assert "Man City" in probs and "Bournemouth" in probs
    assert probs["Man City"] > probs["Bournemouth"]


async def test_fetch_win_probs_from_odds_empty_when_no_market():
    session = _FakeSession({"response": []})

    probs = await run_soccer.fetch_win_probs_from_odds(session, "123", "Man City", "Bournemouth")

    assert probs == {}


async def test_on_trigger_enriches_event_with_pre_and_at_trigger_odds(monkeypatch):
    sent = []

    async def fake_dispatch(alert):
        sent.append(alert)

    logged = []
    monkeypatch.setattr(run_soccer, "log_trigger", lambda event: logged.append(event))
    monkeypatch.setattr(run_soccer.router, "dispatch", fake_dispatch)

    session = _FakeSession(_odds_payload(1.3, 5.0, 8.0))  # market has since shortened on the favorite
    pre_match = {"Man City": 0.60, "Bournemouth": 0.15}
    event = TriggerEvent(game_id="g1", rule_name="red_card_state_shift", message="test", market_hint="test")

    await run_soccer.on_trigger(event, session, "123", "Man City", "Bournemouth", pre_match)

    assert event.metadata["odds_pre_match"] == pre_match
    assert event.metadata["odds_at_trigger"]["Man City"] > pre_match["Man City"]  # market moved further in City's favor
    assert len(logged) == 1
    assert len(sent) == 1


async def test_on_trigger_survives_odds_fetch_failure(monkeypatch):
    monkeypatch.setattr(run_soccer, "log_trigger", lambda event: None)

    async def fake_dispatch(alert):
        pass

    monkeypatch.setattr(run_soccer.router, "dispatch", fake_dispatch)

    async def broken_fetch(session, fixture_id, home_name, away_name):
        raise RuntimeError("network blip")

    monkeypatch.setattr(run_soccer, "fetch_win_probs_from_odds", broken_fetch)

    event = TriggerEvent(game_id="g1", rule_name="red_card_state_shift", message="test", market_hint="test")
    # should not raise, and should log with an empty odds_at_trigger instead
    await run_soccer.on_trigger(event, session=None, fixture_id="123", home_name="A", away_name="B", pre_match_probs={"A": 0.6, "B": 0.2})

    assert event.metadata["odds_at_trigger"] == {}
