"""Domain objects parsed out of ESPN's league document."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .constants import (
    NON_STARTING_SLOTS,
    POSITION_NAMES,
    PRO_TEAM_ABBREV,
    SLOT_NAMES,
    STAT_SOURCE_ACTUAL,
    STAT_SOURCE_PROJECTED,
    STAT_SPLIT_WEEKLY,
)


@dataclass
class Player:
    player_id: int
    name: str
    position: str
    pro_team_id: int
    eligible_slots: List[int]
    current_slot: int
    injury_status: str
    projected_points: float
    #: Filled in by the signal layer.
    on_bye: bool = False
    game_started: bool = False
    opponent_id: Optional[int] = None
    matchup_multiplier: float = 1.0
    exclusion_reason: Optional[str] = None

    @property
    def pro_team(self) -> str:
        return PRO_TEAM_ABBREV.get(self.pro_team_id, str(self.pro_team_id))

    @property
    def slot_name(self) -> str:
        return SLOT_NAMES.get(self.current_slot, str(self.current_slot))

    @property
    def is_starting(self) -> bool:
        return self.current_slot not in NON_STARTING_SLOTS

    @property
    def score(self) -> float:
        """Projection after matchup and injury adjustments.

        Zero for anyone who cannot help this week, so the optimizer will only
        start them if a slot has no alternative.
        """
        if self.exclusion_reason:
            return 0.0
        return self.projected_points * self.matchup_multiplier

    def describe(self) -> str:
        bits = [f"{self.name} ({self.position}, {self.pro_team})"]
        if self.exclusion_reason:
            bits.append(f"— {self.exclusion_reason}")
        return " ".join(bits)


@dataclass
class Roster:
    team_id: int
    team_name: str
    players: List[Player] = field(default_factory=list)

    def by_id(self, player_id: int) -> Optional[Player]:
        return next((p for p in self.players if p.player_id == player_id), None)


def _weekly_stat(player: Dict[str, Any], week: int, source: int) -> Optional[float]:
    for stat in player.get("stats") or []:
        if (
            stat.get("scoringPeriodId") == week
            and stat.get("statSourceId") == source
            and stat.get("statSplitTypeId") == STAT_SPLIT_WEEKLY
        ):
            total = stat.get("appliedTotal")
            if total is not None:
                return float(total)
    return None


def projected_points(player: Dict[str, Any], week: int) -> float:
    """League-scored projection for ``week``, or 0.0 when ESPN has none."""
    value = _weekly_stat(player, week, STAT_SOURCE_PROJECTED)
    return value if value is not None else 0.0


def actual_points(player: Dict[str, Any], week: int) -> Optional[float]:
    return _weekly_stat(player, week, STAT_SOURCE_ACTUAL)


def parse_roster(team: Dict[str, Any], week: int) -> Roster:
    """Build a :class:`Roster` from one entry of the league ``teams`` array."""
    name = team.get("name") or " ".join(
        filter(None, [team.get("location"), team.get("nickname")])
    ).strip() or f"Team {team.get('id')}"

    roster = Roster(team_id=team["id"], team_name=name)
    for entry in (team.get("roster") or {}).get("entries") or []:
        pool = entry.get("playerPoolEntry") or {}
        raw = pool.get("player") or {}
        if not raw.get("id"):
            continue
        roster.players.append(
            Player(
                player_id=raw["id"],
                name=raw.get("fullName", f"Player {raw['id']}"),
                position=POSITION_NAMES.get(raw.get("defaultPositionId"), "?"),
                pro_team_id=raw.get("proTeamId", 0),
                eligible_slots=list(raw.get("eligibleSlots") or []),
                current_slot=entry.get("lineupSlotId", 20),
                injury_status=(raw.get("injuryStatus") or "ACTIVE").upper(),
                projected_points=projected_points(raw, week),
            )
        )
    return roster


def starting_slot_counts(league: Dict[str, Any]) -> Dict[int, int]:
    """Map of slot id -> number of starters, excluding bench and IR."""
    counts = (
        (league.get("settings") or {})
        .get("rosterSettings", {})
        .get("lineupSlotCounts", {})
    )
    return {
        int(slot): int(n)
        for slot, n in counts.items()
        if int(n) > 0 and int(slot) not in NON_STARTING_SLOTS
    }
