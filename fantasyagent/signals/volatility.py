"""Opponent-aware floor/ceiling tilt.

Maximizing your own expected points is the right objective in an average
week, but not when you're a big underdog (you need variance) or a big
favorite (you should minimize it). This computes each player's own
game-to-game volatility from their actual scoring history, then nudges
players up or down based on how far the matchup projects to be decided by —
entirely from data this package already fetches reliably (ESPN's own weekly
actual points), so it adds no new external dependency.

The tilt is capped modestly: it should break ties in the right direction,
not override a real projection gap.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

from ..espn.models import Player, actual_points

#: Minimum completed games before a player's volatility is trusted at all.
MIN_GAMES = 3
#: How much a maximal tilt can move a score, at either extreme.
MAX_TILT = 0.08
#: Matchup margin (projected-point gap) beyond which the tilt is fully applied.
FULL_TILT_MARGIN = 25.0


class VolatilityModel:
    """Coefficient of variation (stdev / mean) of weekly actual points,
    keyed by player id — a scale-free measure of how boom/bust someone is."""

    def __init__(self, cv_by_player: Dict[int, float]) -> None:
        self._cv = cv_by_player

    def is_empty(self) -> bool:
        return not self._cv

    def coefficient_of_variation(self, player_id: int) -> Optional[float]:
        return self._cv.get(player_id)


def build(players_payload: Iterable[Dict[str, Any]], through_week: int) -> VolatilityModel:
    cv: Dict[int, float] = {}
    for raw in players_payload:
        player_id = raw.get("id")
        if player_id is None:
            continue
        weekly = [
            actual_points(raw, week)
            for week in range(1, through_week)
        ]
        scores = [p for p in weekly if p is not None]
        if len(scores) < MIN_GAMES:
            continue
        mean = statistics.fmean(scores)
        if mean <= 0:
            continue
        cv[player_id] = statistics.pstdev(scores) / mean
    return VolatilityModel(cv)


def tilt_factor(projected_margin: float) -> float:
    """How much of the max tilt to apply, given how lopsided the matchup is.

    Positive margin = you're favored (want to reduce variance, so this
    returns a negative fraction); negative margin = you're the underdog
    (want more variance, positive fraction). Zero within a close game.
    """
    fraction = -projected_margin / FULL_TILT_MARGIN
    return max(-1.0, min(1.0, fraction))


def apply(players: Iterable[Player], model: VolatilityModel, projected_margin: float) -> None:
    """Nudge each player's score toward the ceiling (underdog) or floor
    (favorite) based on their own historical volatility."""
    if model.is_empty():
        return
    factor = tilt_factor(projected_margin)
    if factor == 0.0:
        return
    for player in players:
        if player.exclusion_reason or player.forced_bench:
            continue
        cv = model.coefficient_of_variation(player.player_id)
        if cv is None:
            continue
        # A CV around 0.5 is typical; above that is meaningfully boom/bust.
        volatility_signal = max(-1.0, min(1.0, (cv - 0.5) / 0.5))
        adjustment = MAX_TILT * factor * volatility_signal
        player.matchup_multiplier *= 1.0 + adjustment
        if adjustment >= 0.01:
            player.reasons.append("leaning ceiling — you're an underdog this week")
        elif adjustment <= -0.01:
            player.reasons.append("leaning floor — you're favored this week")
