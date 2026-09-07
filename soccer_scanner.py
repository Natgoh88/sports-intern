"""
soccer_scanner.py

Trigger engine for soccer play-by-play/event feeds (EPL, Champions
League, and the other major European leagues). Two rule sets:

1. RedCardStateShiftTrigger
   A pre-match favorite goes down to 10 men, or concedes, before
   minute 20. The market usually hasn't fully repriced the game state
   yet in the minutes right after it happens.
   Target: opponent live moneyline/Asian handicap, or a fresh look at
   the match total.

2. LateCornerCardPressureTrigger
   From minute 75 on, a favored team trailing by exactly one goal
   starts visibly forcing the issue - shots and corners spike as they
   push numbers forward. That tends to also mean more fouls from
   stretched defenses and time-wasting from the team in front.
   Target: live corners over, cards over.

Same GameState -> TriggerRule -> TriggerEvent shape as
basketball_scanner.py, so it drops into the same TriggerEngine /
alert_dispatcher pipeline - see README for wiring a shared engine
across both sports if you'd rather not run two polling loops.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TeamMatchState:
    team_id: str
    name: str
    pre_match_win_prob: float  # from de-vigged pre-match odds, 0-1 (see clv_engine.py)
    red_cards: int = 0
    shots: int = 0
    corners: int = 0
    fouls_committed: int = 0
    # rolling 10-minute windows, maintained by whatever adapter feeds this
    shots_last_10min: int = 0
    corners_last_10min: int = 0
    # prior shots_last_10min/corners_last_10min readings from earlier in
    # this same match, oldest first, NOT including the current reading -
    # maintained by whatever wiring layer feeds this (see run_soccer.py).
    # Used by LateCornerCardPressureZScoreTrigger to judge a spike
    # against the team's own match so far instead of a fixed constant.
    shots_10min_history: list[int] = field(default_factory=list)
    corners_10min_history: list[int] = field(default_factory=list)


@dataclass
class SoccerGameState:
    game_id: str
    home: TeamMatchState
    away: TeamMatchState
    minute: int = 0
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


class TriggerRule:
    name = "base"

    def __init__(self):
        self._fired: set[str] = set()

    def evaluate(self, state: SoccerGameState) -> Optional[TriggerEvent]:
        raise NotImplementedError


class RedCardStateShiftTrigger(TriggerRule):
    name = "red_card_state_shift"

    def __init__(self, favorite_prob_threshold: float = 0.65, minute_cutoff: int = 20):
        super().__init__()
        self.favorite_prob_threshold = favorite_prob_threshold
        self.minute_cutoff = minute_cutoff

    def evaluate(self, state: SoccerGameState) -> Optional[TriggerEvent]:
        if state.minute > self.minute_cutoff:
            return None

        pairs = (
            (state.home, state.away, state.home_score, state.away_score),
            (state.away, state.home, state.away_score, state.home_score),
        )
        for team, opponent, team_score, opp_score in pairs:
            if team.pre_match_win_prob < self.favorite_prob_threshold:
                continue

            dedupe_key = f"{state.game_id}:{team.team_id}:{self.name}"
            if dedupe_key in self._fired:
                continue

            went_down_a_man = team.red_cards >= 1
            conceded_early = team_score < opp_score
            if not (went_down_a_man or conceded_early):
                continue

            self._fired.add(dedupe_key)
            reason = "went down to 10 men" if went_down_a_man else "conceded"
            return TriggerEvent(
                game_id=state.game_id,
                rule_name=self.name,
                message=(
                    f"{team.name} (pre-match favorite, {team.pre_match_win_prob:.0%} win prob) "
                    f"{reason} in minute {state.minute}. Market likely hasn't fully repriced yet."
                ),
                market_hint=f"{opponent.name} live moneyline / Asian handicap value, or reassess the match total",
                metadata={"team": team.team_id, "minute": state.minute, "red_cards": team.red_cards},
            )
        return None


class LateCornerCardPressureTrigger(TriggerRule):
    name = "late_pressure_cooker"

    def __init__(self, minute_start: int = 75, shot_spike_threshold: int = 3, corner_spike_threshold: int = 2):
        super().__init__()
        self.minute_start = minute_start
        self.shot_spike_threshold = shot_spike_threshold
        self.corner_spike_threshold = corner_spike_threshold

    def evaluate(self, state: SoccerGameState) -> Optional[TriggerEvent]:
        if state.minute < self.minute_start:
            return None

        pairs = (
            (state.home, state.away, state.home_score, state.away_score),
            (state.away, state.home, state.away_score, state.home_score),
        )
        for team, opponent, team_score, opp_score in pairs:
            trailing_by_one = (opp_score - team_score) == 1
            is_favorite = team.pre_match_win_prob >= 0.5
            if not (trailing_by_one and is_favorite):
                continue

            spiking = team.shots_last_10min >= self.shot_spike_threshold or team.corners_last_10min >= self.corner_spike_threshold
            if not spiking:
                continue

            dedupe_key = f"{state.game_id}:{team.team_id}:{self.name}"
            if dedupe_key in self._fired:
                continue
            self._fired.add(dedupe_key)

            return TriggerEvent(
                game_id=state.game_id,
                rule_name=self.name,
                message=(
                    f"{team.name} trailing by one late (minute {state.minute}), throwing numbers "
                    f"forward: {team.shots_last_10min} shots / {team.corners_last_10min} corners "
                    f"in the last 10 minutes."
                ),
                market_hint="Live corners OVER, cards OVER (desperate challenges + defensive time-wasting)",
                metadata={"team": team.team_id, "minute": state.minute},
            )
        return None


class LateCornerCardPressureZScoreTrigger(TriggerRule):
    """Statistically-adaptive sibling of LateCornerCardPressureTrigger.
    Instead of a fixed "3 shots in 10 minutes" constant, flags a spike
    only when the team's current rate is a real outlier (z-score)
    relative to ITS OWN rate earlier in the same match - a team that's
    been averaging 5 shots per 10 minutes hitting 3 again isn't a
    spike, but a team that's been averaging 1 hitting 3 is, and a fixed
    threshold can't tell those apart. Requires min_history_samples
    prior readings before it will fire at all; with no real basis for
    "normal" yet, it stays silent rather than guessing - unlike the
    fixed-threshold trigger it runs alongside, not in place of, so
    per-rule CLV tracking (clv_engine.BetLogger.summary_by_rule()) can
    show which style actually finds a better edge over real usage.
    """

    name = "late_pressure_cooker_zscore"

    def __init__(self, minute_start: int = 75, z_threshold: float = 1.5, min_history_samples: int = 3):
        super().__init__()
        self.minute_start = minute_start
        self.z_threshold = z_threshold
        self.min_history_samples = min_history_samples

    @staticmethod
    def _zscore(current: int, history: list[int]) -> Optional[float]:
        if len(history) < 2:
            return None
        mean = sum(history) / len(history)
        variance = sum((x - mean) ** 2 for x in history) / len(history)
        stdev = variance**0.5
        if stdev == 0:
            return None  # no variance in the baseline - a z-score against it is meaningless
        return (current - mean) / stdev

    def evaluate(self, state: SoccerGameState) -> Optional[TriggerEvent]:
        if state.minute < self.minute_start:
            return None

        pairs = (
            (state.home, state.away, state.home_score, state.away_score),
            (state.away, state.home, state.away_score, state.home_score),
        )
        for team, opponent, team_score, opp_score in pairs:
            trailing_by_one = (opp_score - team_score) == 1
            is_favorite = team.pre_match_win_prob >= 0.5
            if not (trailing_by_one and is_favorite):
                continue

            if len(team.shots_10min_history) < self.min_history_samples:
                continue  # not enough of this team's own match history yet to judge a spike

            shot_z = self._zscore(team.shots_last_10min, team.shots_10min_history)
            corner_z = self._zscore(team.corners_last_10min, team.corners_10min_history)
            spiking = (shot_z is not None and shot_z >= self.z_threshold) or (corner_z is not None and corner_z >= self.z_threshold)
            if not spiking:
                continue

            dedupe_key = f"{state.game_id}:{team.team_id}:{self.name}"
            if dedupe_key in self._fired:
                continue
            self._fired.add(dedupe_key)

            best_z = max(z for z in (shot_z, corner_z) if z is not None)
            return TriggerEvent(
                game_id=state.game_id,
                rule_name=self.name,
                message=(
                    f"{team.name} trailing by one late (minute {state.minute}), shot/corner rate "
                    f"is a statistical outlier vs its own match average (z={best_z:.1f})."
                ),
                market_hint="Live corners OVER, cards OVER (desperate challenges + defensive time-wasting)",
                metadata={"team": team.team_id, "minute": state.minute, "shot_z": shot_z, "corner_z": corner_z},
            )
        return None


def _demo():
    home = TeamMatchState(team_id="MCI", name="Man City", pre_match_win_prob=0.72)
    away = TeamMatchState(team_id="BOU", name="Bournemouth", pre_match_win_prob=0.10)
    state = SoccerGameState(game_id="demo-epl-1", home=home, away=away, minute=5)

    red_card_rule = RedCardStateShiftTrigger()
    pressure_rule = LateCornerCardPressureTrigger()

    # City go down to 10 men early
    state.home.red_cards = 1
    state.minute = 18
    event = red_card_rule.evaluate(state)
    if event:
        print(f"[TRIGGER] {event.rule_name} | {event.message} | -> {event.market_hint}")

    # fast forward: City trailing 0-1 late, pushing numbers forward
    state.minute = 80
    state.away_score = 1
    state.home.shots_last_10min = 4
    state.home.corners_last_10min = 3
    event = pressure_rule.evaluate(state)
    if event:
        print(f"[TRIGGER] {event.rule_name} | {event.message} | -> {event.market_hint}")


if __name__ == "__main__":
    _demo()
