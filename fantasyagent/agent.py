"""Orchestration: gather signals, decide a lineup, propose it, apply it."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .config import Config
from .espn.client import ESPNClient, ESPNError
from .espn.constants import SLOT_BENCH
from .espn.models import Roster, parse_roster, starting_slot_counts
from .optimizer import Lineup, optimize
from .signals import availability, matchup
from .signals.schedule import WeekSchedule, season_opponents

log = logging.getLogger(__name__)

#: How many of the most-owned players to pull when learning defensive strength.
MATCHUP_PLAYER_LIMIT = 300


@dataclass
class Decision:
    week: int
    roster: Roster
    lineup: Lineup
    current_total: float

    @property
    def gain(self) -> float:
        return self.lineup.projected_total - self.current_total


class LineupAgent:
    def __init__(self, config: Config, client: Optional[ESPNClient] = None) -> None:
        self.config = config
        self.client = client or ESPNClient(
            league_id=config.league_id,
            season=config.season,
            swid=config.swid,
            espn_s2=config.espn_s2,
        )

    # ------------------------------------------------------------------ data

    def _find_team(self, teams: List[Dict[str, Any]]) -> Dict[str, Any]:
        if self.config.team_id is not None:
            for team in teams:
                if team.get("id") == self.config.team_id:
                    return team
            raise ESPNError(
                f"No team with id {self.config.team_id} in league {self.config.league_id}. "
                f"Teams present: {[t.get('id') for t in teams]}"
            )

        wanted = (self.config.team_name or "").strip().lower()
        for team in teams:
            candidate = parse_roster(team, 1).team_name.strip().lower()
            if candidate == wanted:
                return team
        raise ESPNError(
            f"No team named {self.config.team_name!r}. Teams present: "
            f"{[parse_roster(t, 1).team_name for t in teams]}"
        )

    def _matchup_model(self, week: int) -> matchup.MatchupModel:
        """Learn points allowed per position from completed weeks."""
        try:
            payload = self.client.league(
                ["kona_player_info"],
                fantasy_filter={
                    "players": {
                        "limit": MATCHUP_PLAYER_LIMIT,
                        "sortPercOwned": {"sortAsc": False, "sortPriority": 1},
                    }
                },
            )
            schedules = self.client.pro_team_schedules()
        except ESPNError as exc:
            # A missing matchup adjustment costs a fraction of a point; a failed
            # run costs the whole week. Degrade instead of dying.
            log.warning("Matchup model unavailable (%s); using projections as-is", exc)
            return matchup.MatchupModel({})

        raw_players = [
            entry.get("player", entry) for entry in (payload.get("players") or [])
        ]
        return matchup.build(
            raw_players,
            season_opponents(schedules, week),
            week,
            alpha=self.config.matchup_alpha,
        )

    # ------------------------------------------------------------- decision

    def decide(self, week: Optional[int] = None, now: Optional[datetime] = None) -> Decision:
        settings = self.client.league(["mSettings"])
        week = week or settings.get("scoringPeriodId")
        if not week:
            raise ESPNError("ESPN did not report a current scoring period for this league.")

        slot_counts = starting_slot_counts(settings)
        if not slot_counts:
            raise ESPNError("Could not read this league's starting-lineup slots.")

        payload = self.client.league(["mRoster", "mTeam"], scoring_period=week)
        team = self._find_team(payload.get("teams") or [])
        roster = parse_roster(team, week)
        if not roster.players:
            raise ESPNError(f"Roster for team {roster.team_id} came back empty.")

        schedules = self.client.pro_team_schedules()
        week_schedule = WeekSchedule.from_espn(schedules, week)
        if week_schedule.is_empty():
            log.warning("No NFL schedule for week %s; skipping bye and lock checks", week)

        availability.apply(
            roster.players,
            week_schedule,
            now=now,
            respect_locks=self.config.respect_locks,
        )
        if self.config.use_matchup:
            matchup.apply(roster.players, self._matchup_model(week))

        current_total = sum(p.score for p in roster.players if p.is_starting)
        lineup = optimize(roster, slot_counts)
        return Decision(week=week, roster=roster, lineup=lineup, current_total=current_total)

    # -------------------------------------------------------------- execute

    def submit(self, decision: Decision, dry_run: bool = False) -> bool:
        """Push the lineup to ESPN. Returns True if a write actually happened."""
        moves = [m.as_payload() for m in decision.lineup.moves]
        if not moves:
            log.info("Nothing to submit; lineup already optimal.")
            return False
        if dry_run:
            log.info("Dry run — would submit %d move(s): %s", len(moves), moves)
            return False

        self.client.submit_lineup(
            team_id=decision.roster.team_id,
            scoring_period=decision.week,
            moves=moves,
        )
        log.info("Submitted %d lineup move(s) for week %s", len(moves), decision.week)
        return True
