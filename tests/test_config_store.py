import config_store


def test_load_config_returns_defaults_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(config_store, "CONFIG_PATH", tmp_path / "config.json")

    cfg = config_store.load_config()

    assert cfg["soccer"]["red_card_state_shift"]["favorite_prob_threshold"] == 0.55
    assert cfg["basketball"]["poll_interval_seconds"] == 20


def test_save_then_load_config_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(config_store, "CONFIG_PATH", tmp_path / "config.json")

    cfg = config_store.load_config()
    cfg["soccer"]["poll_interval_seconds"] = 45
    config_store.save_config(cfg)

    reloaded = config_store.load_config()
    assert reloaded["soccer"]["poll_interval_seconds"] == 45


def test_load_config_backfills_missing_keys_from_older_file(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text('{"soccer": {"poll_interval_seconds": 30}}')
    monkeypatch.setattr(config_store, "CONFIG_PATH", config_path)

    cfg = config_store.load_config()

    assert cfg["soccer"]["poll_interval_seconds"] == 30  # preserved
    assert "red_card_state_shift" in cfg["soccer"]  # backfilled from defaults
    assert "basketball" in cfg  # backfilled entirely


def test_save_env_round_trips_and_preserves_unset_keys(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    monkeypatch.setattr(config_store, "ENV_PATH", env_path)

    config_store.save_env({"TELEGRAM_BOT_TOKEN": "abc123", "TELEGRAM_CHAT_ID": "999"})
    config_store.save_env({"API_FOOTBALL_KEY": "key1"})

    values = config_store.load_env()
    assert values["TELEGRAM_BOT_TOKEN"] == "abc123"
    assert values["TELEGRAM_CHAT_ID"] == "999"
    assert values["API_FOOTBALL_KEY"] == "key1"


def test_load_env_missing_file_returns_empty_values(tmp_path, monkeypatch):
    monkeypatch.setattr(config_store, "ENV_PATH", tmp_path / ".env")

    values = config_store.load_env()

    assert values["TELEGRAM_BOT_TOKEN"] == ""
