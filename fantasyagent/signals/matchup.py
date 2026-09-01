"""Opponent-strength adjustment, derived from ESPN's own scoring history.

We compute fantasy points allowed per position by each NFL defense, using the
league's actual scoring settings, then nudge each projection by where that
defense sits in the distribution.

The nudge is deliberately small. ESPN's projections already price in matchup to
some degree, so a large adjustment would double-count it; the value here is
breaking near-ties between two players projected within a point of each other.
"""

from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..espn.constants import POSITION_NAMES
from ..espn.models import Player, actual_points

log = logging.getLogger(__name__)

#: How many performances per position count as "the players who actually played".
#: Deep positions get more slots because real defenses face two starting RBs/WRs.
STARTERS_PER_GAME = {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "K": 1, "D/ST": 1}

#: Minimum games of history a defense needs before we trust its rating.
MIN_GAMES = 3
#: Minimum defenses with data before the league-wide distribution means anything.
MIN_DEFENSES = 8


class MatchupModel:
    """Points-allowed ratings, keyed by ``(defense_pro_team_id, position)``."""

    def __init__(
        self,
        ratings: Dict[Tuple[int, str], float],
        alpha: float = 0.06,
        clip: Tuple[float, float] = (0.88, 1.12),
    ) -> None:
        self._ratings = ratings
        self._alpha = alpha
        self._clip = clip
        self._distribution = self._build_distribution()

    def _build_distribution(self) -> Dict[str, Tuple[float, float]]:
        by_position: Dict[str, List[float]] = defaultdict(list)
        for (_, position), value in self._ratings.items():
            by_position[position].append(value)
        out: Dict[str, Tuple[float, float]] = {}
        for position, values in by_position.items():
            if len(values) < MIN_DEFENSES:
                continue
            stdev = statistics.pstdev(values)
            if stdev > 0:
                out[position] = (statistics.fmean(values), stdev)
        return out

    def multiplier(self, position: str, defense_id: Optional[int]) -> float:
        """Adjustment factor for ``position`` facing ``defense_id``."""
        if defense_id is None:
            return 1.0
        rating = self._ratings.get((defense_id, position))
        moments = self._distribution.get(position)
        if rating is None or moments is None:
            return 1.0
        mean, stdev = moments
        z = (rating - mean) / stdev
        low, high = self._clip
        return max(low, min(high, 1.0 + self._alpha * z))

    def is_empty(self) -> bool:
        return not self._distribution


def build(
    players_payload: Iterable[Dict[str, Any]],
    opponents: Dict[int, Dict[int, int]],
    through_week: int,
    alpha: float = 0.06,
) -> MatchupModel:
    """Compute points-allowed ratings from completed weeks.

    ``players_payload`` is ESPN's raw player list (the ``kona_player_info``
    view); ``opponents`` maps pro team -> week -> opponent.
    """
    # (defense, position, week) -> scores put up against it that week
    game_logs: Dict[Tuple[int, str, int], List[float]] = defaultdict(list)

    for raw in players_payload:
        position = POSITION_NAMES.get(raw.get("defaultPositionId"))
        pro_team_id = raw.get("proTeamId")
        if not position or not pro_team_id:
            continue
        team_weeks = opponents.get(pro_team_id, {})
        for week in range(1, through_week):  # completed weeks only
            points = actual_points(raw, week)
            defense = team_weeks.get(week)
            if points is None or defense is None:
                continue
            game_logs[(defense, position, week)].append(points)

    # Per game, keep only the top performances so bench scrubs do not drag a
    # defense's rating down for reasons unrelated to that defense.
    totals: Dict[Tuple[int, str], List[float]] = defaultdict(list)
    for (defense, position, _week), scores in game_logs.items():
        keep = STARTERS_PER_GAME.get(position, 1)
        scores.sort(reverse=True)
        totals[(defense, position)].append(statistics.fmean(scores[:keep]))

    ratings = {
        key: statistics.fmean(values)
        for key, values in totals.items()
        if len(values) >= MIN_GAMES
    }
    log.debug("matchup model built from %d defense/position ratings", len(ratings))
    return MatchupModel(ratings, alpha=alpha)


def apply(players: Iterable[Player], model: MatchupModel) -> None:
    """Fold each player's matchup adjustment into their multiplier."""
    if model.is_empty():
        return
    for player in players:
        if player.exclusion_reason:
            continue
        player.matchup_multiplier *= model.multiplier(player.position, player.opponent_id)
