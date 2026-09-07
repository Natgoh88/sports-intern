"""
config_store.py

Central place to read/write the two things this app treats as user
config: .env (secrets) and config.json (trigger thresholds + key
players). Both the dashboard's Settings page and the two run_*.py
scanners import from here, so there's exactly one place that knows the
file formats and default values.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent
ENV_PATH = BASE_DIR / ".env"
CONFIG_PATH = BASE_DIR / "config.json"

DEFAULT_CONFIG = {
    "basketball": {
        "poll_interval_seconds": 20,
        "bonus_trigger": {"min_seconds_remaining": 360},
        "foul_trouble_trigger": {"foul_count_threshold": 2, "early_period_cutoff": 1},
        "key_players": {
            "PHI": {"Joel Embiid": "rim_protector"},
            "BOS": {"Jayson Tatum": "primary_scorer"},
        },
    },
    "soccer": {
        "poll_interval_seconds": 60,
        # 0.55 not 0.65: on a real 3-way (home/draw/away) market, even
        # clear favorites rarely clear 65% fair win probability once the
        # draw is priced out - confirmed against real UCL odds (Real
        # Madrid ~60%, Man City ~56%) on 2026-09-07. 0.65 would silently
        # never fire on most real matchups.
        "red_card_state_shift": {"favorite_prob_threshold": 0.55, "minute_cutoff": 20},
        "late_pressure_cooker": {"minute_start": 75, "shot_spike_threshold": 3, "corner_spike_threshold": 2},
    },
}

ENV_KEYS = [
    "DISCORD_WEBHOOK_URL",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "API_FOOTBALL_KEY",
    "TRIGGER_LOG_PATH",
    "BETS_DB_PATH",
]


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    # backfill any keys missing from an older config.json instead of
    # crashing on a KeyError when new settings get added later
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    _deep_update(merged, cfg)
    return merged


def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


def _deep_update(base: dict, updates: dict) -> None:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = value


def load_env() -> dict[str, str]:
    values = {key: "" for key in ENV_KEYS}
    if not ENV_PATH.exists():
        return values
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key in values:
                values[key] = value.strip()
    return values


def save_env(values: dict[str, str]) -> None:
    """Rewrites .env from scratch in a fixed, commented layout. Anything
    not in ENV_KEYS is dropped - this file only ever holds the vars this
    app actually reads, so that's not a real loss."""
    merged = load_env()
    merged.update({k: v for k, v in values.items() if k in ENV_KEYS})

    lines = [
        "# Discord: Server Settings -> Integrations -> Webhooks -> New Webhook -> Copy URL",
        f"DISCORD_WEBHOOK_URL={merged['DISCORD_WEBHOOK_URL']}",
        "",
        "# Telegram: message @BotFather -> /newbot -> copy the token",
        f"TELEGRAM_BOT_TOKEN={merged['TELEGRAM_BOT_TOKEN']}",
        "# Add your bot to the target chat/channel, then GET",
        "# https://api.telegram.org/bot<token>/getUpdates to read the chat_id",
        f"TELEGRAM_CHAT_ID={merged['TELEGRAM_CHAT_ID']}",
        "",
        "# API-Football (api-sports.io): dashboard.api-football.com -> profile -> API keys",
        f"API_FOOTBALL_KEY={merged['API_FOOTBALL_KEY']}",
        "",
        "# Only needed if you point dashboard.py or the scanners at non-default paths",
        f"TRIGGER_LOG_PATH={merged['TRIGGER_LOG_PATH'] or 'triggers.log.jsonl'}",
        f"BETS_DB_PATH={merged['BETS_DB_PATH'] or 'bets.db'}",
        "",
    ]
    with open(ENV_PATH, "w") as f:
        f.write("\n".join(lines))

    # keep os.environ in sync for the current process without requiring a restart
    for key, value in merged.items():
        if value:
            os.environ[key] = value
