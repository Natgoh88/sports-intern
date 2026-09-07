from datetime import datetime, timedelta, timezone

from run_soccer import active_window, next_window_start

CFG = {
    "window1_wallclock_minutes": 30,
    "window2_start_offset_minutes": 85,
    "window2_end_offset_minutes": 120,
}


def test_active_window_none_before_kickoff():
    kickoff = datetime(2026, 9, 8, 19, 0, tzinfo=timezone.utc)
    now = kickoff - timedelta(minutes=5)
    assert active_window(kickoff, now, CFG) is None


def test_active_window_window1_right_after_kickoff():
    kickoff = datetime(2026, 9, 8, 19, 0, tzinfo=timezone.utc)
    now = kickoff + timedelta(minutes=10)
    assert active_window(kickoff, now, CFG) == "window1"


def test_active_window_none_in_quiet_period():
    kickoff = datetime(2026, 9, 8, 19, 0, tzinfo=timezone.utc)
    now = kickoff + timedelta(minutes=50)  # past window1, before window2
    assert active_window(kickoff, now, CFG) is None


def test_active_window_window2_late_in_match():
    kickoff = datetime(2026, 9, 8, 19, 0, tzinfo=timezone.utc)
    now = kickoff + timedelta(minutes=100)
    assert active_window(kickoff, now, CFG) == "window2"


def test_active_window_none_after_match_over():
    kickoff = datetime(2026, 9, 8, 19, 0, tzinfo=timezone.utc)
    now = kickoff + timedelta(minutes=130)
    assert active_window(kickoff, now, CFG) is None


def test_next_window_start_before_kickoff_returns_window1():
    kickoff = datetime(2026, 9, 8, 19, 0, tzinfo=timezone.utc)
    now = kickoff - timedelta(minutes=30)
    assert next_window_start(kickoff, now, CFG) == kickoff


def test_next_window_start_during_quiet_period_returns_window2():
    kickoff = datetime(2026, 9, 8, 19, 0, tzinfo=timezone.utc)
    now = kickoff + timedelta(minutes=50)
    expected = kickoff + timedelta(minutes=CFG["window2_start_offset_minutes"])
    assert next_window_start(kickoff, now, CFG) == expected


def test_next_window_start_after_both_windows_returns_none():
    kickoff = datetime(2026, 9, 8, 19, 0, tzinfo=timezone.utc)
    now = kickoff + timedelta(minutes=130)
    assert next_window_start(kickoff, now, CFG) is None
