import scanner_manager


def test_heartbeat_age_seconds_missing_file_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner_manager, "RUN_DIR", tmp_path)
    assert scanner_manager.heartbeat_age_seconds("soccer") is None


def test_write_heartbeat_then_age_is_small(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner_manager, "RUN_DIR", tmp_path)

    scanner_manager.write_heartbeat("soccer")
    age = scanner_manager.heartbeat_age_seconds("soccer")

    assert age is not None
    assert 0 <= age < 5


def test_heartbeat_age_seconds_corrupt_file_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner_manager, "RUN_DIR", tmp_path)
    (tmp_path / "soccer.heartbeat").write_text("not-a-number")

    assert scanner_manager.heartbeat_age_seconds("soccer") is None
