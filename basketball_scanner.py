"""
basketball_scanner.py

Event-driven trigger engine for NBA / NCAA men's basketball play-by-play
feeds. Two rule sets:

1. BonusTrigger ("both teams in the bonus early" / Trap God 9)
   Fires when both teams have crossed the bonus foul threshold for the
   period with real time still on the clock. Once both sides are in
   the bonus, every non-shooting foul turns into free points, and
   defenses get more cautious around the rim to avoid adding to the
   total. That combination tends to push pace and scoring up.
   Target: live period/game total, lean over.

2. FoulTroubleTrigger
   Fires when a tagged "load-bearing" player (primary rim protector or
   high-usage scorer) picks up 2 fouls in the first period (NBA) or
   first half (NCAA). Flags a likely rotation change and defensive/pace
   swing before the market has fully adjusted.

Both rules run inside a single TriggerEngine that polls a pluggable
PlayByPlayAdapter per game on a fixed interval and dedupes so the same
trigger doesn't fire every single polling cycle.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Protocol


class League(str, Enum):
    NBA = "NBA"
    NCAAM = "NCAAM"


# Bonus rules differ by league. NBA resets team fouls every quarter and
# the bonus kicks in at 5. NCAA men play halves, bonus (1-and-1) at 7
# team fouls, double bonus (2 shots) at 10. Keep these in a config dict
# instead of hardcoding magic numbers through the rule logic itself -
# these have changed before and will again.
BONUS_RULES = {
    League.NBA: {"period_length_min": 12, "bonus_at": 5},
    League.NCAAM: {"period_length_min": 20, "bonus_at": 7, "double_bonus_at": 10},
}


@dataclass
class PlayerFoulState:
    player_id: str
    name: str
    fouls: int = 0
    is_key_player: bool = False  # tag from your own roster/usage config
    role: str = ""  # e.g. "rim_protector", "primary_scorer"


@dataclass
class TeamState:
    team_id: str
    name: str
    period_fouls: int = 0
    players: dict[str, PlayerFoulState] = field(default_factory=dict)
    in_bonus: bool = False


@dataclass
class GameState:
    game_id: str
    league: League
    home: TeamState
    away: TeamState
    period: int = 1
    clock_seconds_remaining: int = 0  # seconds left in the current period
    home_score: int = 0
    away_score: int = 0
    last_updated: float = field(default_factory=time.time)


@dataclass
class TriggerEvent:
    game_id: str
    rule_name: str
    message: str
    market_hint: str
    fired_at: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)


class PlayByPlayAdapter(Protocol):
    async def active_games(self) -> list[str]: ...
    async def poll(self, game_id: str) -> GameState: ...


# ---------------------------------------------------------------------------
# Trigger rules
# ---------------------------------------------------------------------------

class TriggerRule:
    name = "base"

    def __init__(self):
        self._fired: set[str] = set()  # dedupe keys already emitted

    def evaluate(self, state: GameState) -> Optional[TriggerEvent]:
        raise NotImplementedError


class BonusTrigger(TriggerRule):
    """Trap God 9: both teams in the bonus with meaningful time left."""

    name = "bonus_trigger"

    def __init__(self, min_seconds_remaining: int = 360):  # default 6:00
        super().__init__()
        self.min_seconds_remaining = min_seconds_remaining

    def evaluate(self, state: GameState) -> Optional[TriggerEvent]:
        bonus_at = BONUS_RULES[state.league]["bonus_at"]
        state.home.in_bonus = state.home.period_fouls >= bonus_at
        state.away.in_bonus = state.away.period_fouls >= bonus_at

        if not (state.home.in_bonus and state.away.in_bonus):
            return None
        if state.clock_seconds_remaining < self.min_seconds_remaining:
            return None

        dedupe_key = f"{state.game_id}:{state.period}:{self.name}"
        if dedupe_key in self._fired:
            return None
        self._fired.add(dedupe_key)

        minutes_left = state.clock_seconds_remaining // 60
        return TriggerEvent(
            game_id=state.game_id,
            rule_name=self.name,
            message=(
                f"Both teams in the bonus in period {state.period} with "
                f"~{minutes_left} min left. {state.home.name} "
                f"({state.home.period_fouls} fouls) vs {state.away.name} "
                f"({state.away.period_fouls} fouls)."
            ),
            market_hint=f"Live period {state.period} total - lean OVER",
            metadata={
                "period": state.period,
                "home_fouls": state.home.period_fouls,
                "away_fouls": state.away.period_fouls,
                "clock_seconds_remaining": state.clock_seconds_remaining,
            },
        )


class FoulTroubleTrigger(TriggerRule):
    """Tagged key player picks up 2 fouls early, before it's priced in."""

    name = "foul_trouble_trigger"

    def __init__(self, foul_count_threshold: int = 2, early_period_cutoff: int = 1):
        super().__init__()
        self.foul_count_threshold = foul_count_threshold
        self.early_period_cutoff = early_period_cutoff  # only fires at period <= this

    def evaluate(self, state: GameState) -> Optional[TriggerEvent]:
        if state.period > self.early_period_cutoff:
            return None

        for team in (state.home, state.away):
            for player in team.players.values():
                if not player.is_key_player or player.fouls < self.foul_count_threshold:
                    continue

                dedupe_key = f"{state.game_id}:{player.player_id}:{self.name}"
                if dedupe_key in self._fired:
                    continue
                self._fired.add(dedupe_key)

                return TriggerEvent(
                    game_id=state.game_id,
                    rule_name=self.name,
                    message=(
                        f"{player.name} ({team.name}, {player.role or 'key player'}) "
                        f"picked up {player.fouls} fouls in period {state.period}. "
                        f"Watch for early bench minutes and a pace/defensive shift."
                    ),
                    market_hint=(
                        f"{team.name} short-term live total UNDER while {player.name} sits, "
                        f"or backup minutes/usage props"
                    ),
                    metadata={"player_id": player.player_id, "team": team.team_id, "period": state.period},
                )
        return None


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class TriggerEngine:
    """
    Polls every active game concurrently on a fixed interval, runs each
    rule against the fresh GameState, and hands any resulting
    TriggerEvent to on_trigger (wire this to alert_dispatcher.AlertRouter
    in production).
    """

    def __init__(self, adapter: PlayByPlayAdapter, rules: list[TriggerRule], on_trigger, poll_interval_seconds: int = 15):
        self.adapter = adapter
        self.rules = rules
        self.on_trigger = on_trigger
        self.poll_interval_seconds = poll_interval_seconds
        self._running = False

    async def _scan_game(self, game_id: str):
        state = await self.adapter.poll(game_id)
        for rule in self.rules:
            event = rule.evaluate(state)
            if event:
                await self.on_trigger(event)

    async def run(self):
        self._running = True
        while self._running:
            try:
                game_ids = await self.adapter.active_games()
                # return_exceptions=True: one game's bad response
                # shouldn't cancel every other in-flight poll this tick
                results = await asyncio.gather(*(self._scan_game(gid) for gid in game_ids), return_exceptions=True)
                for game_id, result in zip(game_ids, results):
                    if isinstance(result, Exception):
                        print(f"[TriggerEngine] scan failed for {game_id}: {result}")
            except Exception as exc:
                # covers active_games() itself failing - keep the loop
                # alive instead of letting one bad tick kill the scanner
                print(f"[TriggerEngine] tick failed: {exc}")
            await asyncio.sleep(self.poll_interval_seconds)

    def stop(self):
        self._running = False


# ---------------------------------------------------------------------------
# Mock adapter - lets you validate rules + the alert pipeline without a
# live feed wired up yet. Swap this out for a real adapter (see README
# for the ESPN/balldontlie/API-Sports patterns) once you're ready.
# ---------------------------------------------------------------------------

class MockAdapter:
    def __init__(self):
        self._tick = 0
        home = TeamState(
            team_id="PHI",
            name="76ers",
            players={"p1": PlayerFoulState(player_id="p1", name="Joel Embiid", is_key_player=True, role="rim_protector")},
        )
        away = TeamState(
            team_id="BOS",
            name="Celtics",
            players={"p2": PlayerFoulState(player_id="p2", name="Jayson Tatum", is_key_player=True, role="primary_scorer")},
        )
        self.state = GameState(game_id="demo-nba-1", league=League.NBA, home=home, away=away, period=1, clock_seconds_remaining=720)

    async def active_games(self) -> list[str]:
        return [self.state.game_id]

    async def poll(self, game_id: str) -> GameState:
        self._tick += 1
        self.state.clock_seconds_remaining = max(0, self.state.clock_seconds_remaining - 90)
        if self._tick == 2:
            self.state.home.players["p1"].fouls = 2
        if self._tick >= 3:
            self.state.home.period_fouls = 5
            self.state.away.period_fouls = 5
        self.state.last_updated = time.time()
        return self.state


async def _demo():
    async def handle_trigger(event: TriggerEvent):
        print(f"[TRIGGER] {event.rule_name} | {event.message} | -> {event.market_hint}")

    adapter = MockAdapter()
    rules = [BonusTrigger(min_seconds_remaining=300), FoulTroubleTrigger()]

    # run a fixed number of scan cycles for the demo instead of forever
    for _ in range(5):
        for game_id in await adapter.active_games():
            state = await adapter.poll(game_id)
            for rule in rules:
                event = rule.evaluate(state)
                if event:
                    await handle_trigger(event)


if __name__ == "__main__":
    asyncio.run(_demo())
