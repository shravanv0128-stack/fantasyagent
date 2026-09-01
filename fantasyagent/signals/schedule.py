"""NFL schedule facts: who plays this week, against whom, and when kickoff is."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional


@dataclass
class Game:
    opponent_id: int
    kickoff: datetime


class WeekSchedule:
    """Per-pro-team schedule facts for a single scoring period."""

    def __init__(self, games: Dict[int, Game]) -> None:
        self._games = games

    @classmethod
    def from_espn(cls, payload: Dict[str, Any], week: int) -> "WeekSchedule":
        games: Dict[int, Game] = {}
        pro_teams = (payload.get("settings") or {}).get("proTeams") or []
        for team in pro_teams:
            team_id = team.get("id")
            by_period = team.get("proGamesByScoringPeriod") or {}
            for game in by_period.get(str(week)) or []:
                home = game.get("homeProTeamId")
                away = game.get("awayProTeamId")
                opponent = away if team_id == home else home
                date_ms = game.get("date")
                if opponent is None or date_ms is None:
                    continue
                games[team_id] = Game(
                    opponent_id=opponent,
                    kickoff=datetime.fromtimestamp(date_ms / 1000, tz=timezone.utc),
                )
                break
        return cls(games)

    def game_for(self, pro_team_id: int) -> Optional[Game]:
        return self._games.get(pro_team_id)

    def is_on_bye(self, pro_team_id: int) -> bool:
        """True when the team has no game this week.

        Free agents / empty slots carry pro team 0 and are not "on bye"; they
        are excluded elsewhere for having no projection.
        """
        return pro_team_id != 0 and pro_team_id not in self._games

    def has_kicked_off(self, pro_team_id: int, now: Optional[datetime] = None) -> bool:
        game = self._games.get(pro_team_id)
        if game is None:
            return False
        return (now or datetime.now(timezone.utc)) >= game.kickoff

    def is_empty(self) -> bool:
        return not self._games


def season_opponents(payload: Dict[str, Any], through_week: int) -> Dict[int, Dict[int, int]]:
    """``{pro_team_id: {week: opponent_id}}`` for weeks 1..``through_week``."""
    out: Dict[int, Dict[int, int]] = {}
    for team in (payload.get("settings") or {}).get("proTeams") or []:
        team_id = team.get("id")
        if team_id is None:
            continue
        weeks: Dict[int, int] = {}
        for week_str, games in (team.get("proGamesByScoringPeriod") or {}).items():
            try:
                week = int(week_str)
            except (TypeError, ValueError):
                continue
            if not 1 <= week <= through_week:
                continue
            for game in games:
                home, away = game.get("homeProTeamId"), game.get("awayProTeamId")
                opponent = away if team_id == home else home
                if opponent is not None:
                    weeks[week] = opponent
                    break
        out[team_id] = weeks
    return out
