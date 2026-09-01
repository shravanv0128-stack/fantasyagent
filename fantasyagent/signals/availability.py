"""Decide who cannot help this week, and discount who probably will not.

This is the highest-value signal in the whole agent: starting a player who is
ruled OUT or on bye is the single most expensive routine mistake, and unlike
projection quality it is knowable with certainty.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

from ..espn.constants import INJURY_DISCOUNT, INJURY_OUT
from ..espn.models import Player
from .schedule import WeekSchedule


def apply(
    players: Iterable[Player],
    schedule: WeekSchedule,
    *,
    now: Optional[datetime] = None,
    respect_locks: bool = True,
) -> None:
    """Annotate each player in place with availability facts.

    Sets ``exclusion_reason`` for anyone unstartable, ``game_started`` for
    anyone whose kickoff has passed (their slot is locked at ESPN, so we must
    not try to move them), and discounts the projection for QUESTIONABLE.
    """
    now = now or datetime.now(timezone.utc)

    for player in players:
        game = schedule.game_for(player.pro_team_id)
        if game is not None:
            player.opponent_id = game.opponent_id

        # A team with no game this week is on bye. If the schedule failed to
        # load at all we skip the check rather than benching the whole roster.
        if not schedule.is_empty() and schedule.is_on_bye(player.pro_team_id):
            player.on_bye = True
            player.exclusion_reason = "on bye"
            continue

        if player.injury_status in INJURY_OUT:
            player.exclusion_reason = player.injury_status.replace("_", " ").lower()
            continue

        if respect_locks and schedule.has_kicked_off(player.pro_team_id, now):
            # Not an exclusion: they may already have scored. Just immovable.
            player.game_started = True

        discount = INJURY_DISCOUNT.get(player.injury_status)
        if discount:
            player.matchup_multiplier *= discount
