"""
alert_dispatcher.py

Takes an AlertMessage (built from a TriggerEvent produced by either
scanner) and fires it to Discord and/or Telegram immediately. Both
dispatchers implement the same send() interface so the trigger engines
don't need to know which channel they're writing to.

Env vars expected:
    DISCORD_WEBHOOK_URL
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Protocol

import aiohttp


@dataclass
class AlertMessage:
    game_id: str
    sport: str  # "NBA", "NCAAM", "EPL", "UCL", etc.
    rule_name: str
    detail: str
    market_hint: str
    score_line: str  # e.g. "PHI 58 - BOS 61"
    game_clock: str  # e.g. "Q1 6:42" or "18'"


class AlertChannel(Protocol):
    async def send(self, alert: AlertMessage) -> None: ...


class DiscordDispatcher:
    def __init__(self, webhook_url: str | None = None):
        self.webhook_url = webhook_url or os.environ.get("DISCORD_WEBHOOK_URL")
        if not self.webhook_url:
            raise ValueError("DISCORD_WEBHOOK_URL not set")

    async def send(self, alert: AlertMessage) -> None:
        embed = {
            "title": f"{alert.sport} | {alert.rule_name.replace('_', ' ').title()}",
            "description": alert.detail,
            "color": 0x2ECC71,
            "fields": [
                {"name": "Score / Clock", "value": f"{alert.score_line} ({alert.game_clock})", "inline": True},
                {"name": "Target market", "value": alert.market_hint, "inline": False},
            ],
            "footer": {"text": f"game_id: {alert.game_id}"},
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(self.webhook_url, json={"embeds": [embed]}) as resp:
                if resp.status >= 300:
                    raise RuntimeError(f"Discord webhook failed ({resp.status}): {await resp.text()}")


class TelegramDispatcher:
    def __init__(self, bot_token: str | None = None, chat_id: str | None = None):
        self.bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        if not self.bot_token or not self.chat_id:
            raise ValueError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")

    async def send(self, alert: AlertMessage) -> None:
        text = (
            f"*{alert.sport} | {alert.rule_name.replace('_', ' ').title()}*\n"
            f"{alert.detail}\n\n"
            f"Score/Clock: {alert.score_line} ({alert.game_clock})\n"
            f"Target: {alert.market_hint}"
        )
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": "Markdown"}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                if resp.status >= 300:
                    raise RuntimeError(f"Telegram send failed ({resp.status}): {await resp.text()}")


class ConsoleDispatcher:
    """Drop-in channel for local testing - no network calls, just stdout."""

    async def send(self, alert: AlertMessage) -> None:
        print(f"[console] {alert.sport} {alert.rule_name}: {alert.detail} -> {alert.market_hint}")


class AlertRouter:
    """
    Fans one alert out to every configured channel and applies a
    cooldown per (game_id, rule_name) so a chatty rule can't spam the
    same combo repeatedly inside a short window.
    """

    def __init__(self, channels: list[AlertChannel], cooldown_seconds: int = 120):
        self.channels = channels
        self.cooldown_seconds = cooldown_seconds
        self._last_sent: dict[str, float] = {}

    async def dispatch(self, alert: AlertMessage) -> None:
        key = f"{alert.game_id}:{alert.rule_name}"
        now = time.time()
        if now - self._last_sent.get(key, 0) < self.cooldown_seconds:
            return
        self._last_sent[key] = now

        results = await asyncio.gather(*(channel.send(alert) for channel in self.channels), return_exceptions=True)
        for channel, result in zip(self.channels, results):
            if isinstance(result, Exception):
                print(f"[alert_dispatcher] {channel.__class__.__name__} failed: {result}")


if __name__ == "__main__":
    async def _demo():
        router = AlertRouter(channels=[ConsoleDispatcher()], cooldown_seconds=5)
        alert = AlertMessage(
            game_id="demo-1",
            sport="NBA",
            rule_name="bonus_trigger",
            detail="Both teams in the bonus, Q1, 6:12 left.",
            market_hint="Live Q1 total OVER",
            score_line="PHI 24 - BOS 22",
            game_clock="Q1 6:12",
        )
        await router.dispatch(alert)
        await router.dispatch(alert)  # suppressed by the cooldown

    asyncio.run(_demo())
