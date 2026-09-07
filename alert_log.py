"""
alert_log.py

Appends TriggerEvents (from either basketball_scanner or soccer_scanner
- both use the same dataclass shape) to a flat JSONL file so dashboard.py
has something to read. This is not a message queue, just a tail-able
log. Swap in Redis/SQLite if you outgrow it.
"""

import dataclasses
import json


def log_trigger(event, path: str = "triggers.log.jsonl") -> None:
    with open(path, "a") as f:
        f.write(json.dumps(dataclasses.asdict(event)) + "\n")
