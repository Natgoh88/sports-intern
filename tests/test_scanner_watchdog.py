import pytest

import scanner_manager
import scanner_watchdog as watchdog
from alert_dispatcher import AlertMessage


class _FakePath:
    def __init__(self, exists: bool):
        self._exists = exists

    def exists(self):
        return self._exists


class _FakeDispatcher:
    sent: list[AlertMessage] = []

    def __init__(self, *args, **kwargs):
        pass

    async def send(self, alert: AlertMessage):
        _FakeDispatcher.sent.append(alert)


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    watchdog._last_alerted.clear()
    _FakeDispatcher.sent.clear()
    monkeypatch.setattr(watchdog, "TelegramDispatcher", _FakeDispatcher)
    yield


async def test_check_once_does_nothing_if_never_started(monkeypatch):
    monkeypatch.setattr(scanner_manager, "_pid_file", lambda name: _FakePath(exists=False))
    calls = []
    monkeypatch.setattr(scanner_manager, "status", lambda name: calls.append(name) or {"running": True, "pid": 1})

    await watchdog.check_once("soccer")

    assert calls == []  # never even checked status - nothing to watch


async def test_check_once_restarts_dead_scanner(monkeypatch):
    monkeypatch.setattr(scanner_manager, "_pid_file", lambda name: _FakePath(exists=True))
    monkeypatch.setattr(scanner_manager, "status", lambda name: {"running": False, "pid": None})
    started = []
    monkeypatch.setattr(scanner_manager, "start", lambda name: started.append(name))

    await watchdog.check_once("soccer")

    assert started == ["soccer"]
    assert len(_FakeDispatcher.sent) == 1
    assert "stopped unexpectedly" in _FakeDispatcher.sent[0].detail


async def test_check_once_leaves_healthy_scanner_alone(monkeypatch):
    monkeypatch.setattr(scanner_manager, "_pid_file", lambda name: _FakePath(exists=True))
    monkeypatch.setattr(scanner_manager, "status", lambda name: {"running": True, "pid": 123})
    monkeypatch.setattr(scanner_manager, "heartbeat_age_seconds", lambda name: 10.0)
    restart_calls = []
    monkeypatch.setattr(scanner_manager, "start", lambda name: restart_calls.append(name))
    monkeypatch.setattr(scanner_manager, "stop", lambda name: restart_calls.append(name))

    await watchdog.check_once("soccer")

    assert restart_calls == []
    assert _FakeDispatcher.sent == []


async def test_check_once_restarts_hung_scanner_past_threshold(monkeypatch):
    monkeypatch.setattr(scanner_manager, "_pid_file", lambda name: _FakePath(exists=True))
    monkeypatch.setattr(scanner_manager, "status", lambda name: {"running": True, "pid": 123})
    monkeypatch.setattr(scanner_manager, "heartbeat_age_seconds", lambda name: 999_999)
    order = []
    monkeypatch.setattr(scanner_manager, "stop", lambda name: order.append(("stop", name)))
    monkeypatch.setattr(scanner_manager, "start", lambda name: order.append(("start", name)))

    await watchdog.check_once("basketball")

    assert order == [("stop", "basketball"), ("start", "basketball")]
    assert len(_FakeDispatcher.sent) == 1
    assert "hung" in _FakeDispatcher.sent[0].detail


async def test_alert_cooldown_suppresses_repeat_alerts():
    await watchdog._alert("soccer", "first outage message")
    await watchdog._alert("soccer", "second outage message, same outage")

    assert len(_FakeDispatcher.sent) == 1
    assert _FakeDispatcher.sent[0].detail == "first outage message"
