"""
trigger_stats.py

Reads triggers.log.jsonl (the flat file both scanners append to via
alert_log.log_trigger) and aggregates it into per-rule frequency stats -
how often has each rule actually fired, on how many distinct games, and
when most recently. Cheap to recompute on every dashboard load; this
app's own README puts it at "a few dozen concurrent games" scale, so
the file never gets big enough to need anything smarter than a full
re-read.

This exists because a rule that's fired zero times in a month is either
miscalibrated or genuinely rare, and you can't tell which without
counting - see clv_engine.BetLogger.summary_by_rule() for the other
half of that picture (whether the bets placed off a rule are actually
worth anything).
"""

from __future__ import annotations

import json
import os


def load_trigger_rows(path: str | None = None) -> list[dict]:
    path = path or os.environ.get("TRIGGER_LOG_PATH", "triggers.log.jsonl")
    if not os.path.exists(path):
        return []
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def frequency_by_rule(rows: list[dict]) -> dict[str, dict]:
    """Returns {rule_name: {count, distinct_games, last_fired_at}}."""
    stats: dict[str, dict] = {}
    for row in rows:
        rule = row.get("rule_name", "unknown")
        entry = stats.setdefault(rule, {"count": 0, "game_ids": set(), "last_fired_at": None})
        entry["count"] += 1
        entry["game_ids"].add(row.get("game_id"))
        fired_at = row.get("fired_at")
        if fired_at is not None and (entry["last_fired_at"] is None or fired_at > entry["last_fired_at"]):
            entry["last_fired_at"] = fired_at

    return {
        rule: {
            "count": entry["count"],
            "distinct_games": len(entry["game_ids"]),
            "last_fired_at": entry["last_fired_at"],
        }
        for rule, entry in stats.items()
    }
