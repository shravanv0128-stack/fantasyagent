"""Orchestration: gather signals, decide a lineup, propose it, apply it."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .config import Config
from .espn.client import ESPNClient, ESPNError
from .espn.constants import SLOT_BENCH
from .espn.models import Player, Roster, parse_roster, starting_slot_counts
from .optimizer import Lineup, optimize
from .signals import availability, matchup, vegas, volatility, weather
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
    slot_counts: Dict[int, int] = None  # type: ignore[assignment]

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

    def _fetch_raw_players(self) -> List[Dict[str, Any]]:
        """The most-owned players league-wide, with full season stat history.

        Shared by the matchup and volatility models so both are built from
        one ESPN request instead of two.
        """
        payload = self.client.league(
            ["kona_player_info"],
            fantasy_filter={
                "players": {
                    "limit": MATCHUP_PLAYER_LIMIT,
                    "sortPercOwned": {"sortAsc": False, "sortPriority": 1},
                }
            },
        )
        return [entry.get("player", entry) for entry in (payload.get("players") or [])]

    def _find_opponent_team_id(
        self, payload: Dict[str, Any], team_id: int, week: int
    ) -> Optional[int]:
        for entry in payload.get("schedule") or []:
            if entry.get("matchupPeriodId") != week:
                continue
            home = (entry.get("home") or {}).get("teamId")
            away = (entry.get("away") or {}).get("teamId")
            if home == team_id:
                return away
            if away == team_id:
                return home
        return None

    def _opponent_projected_total(
        self,
        payload: Dict[str, Any],
        team_id: int,
        week: int,
        slot_counts: Dict[int, int],
        week_schedule: WeekSchedule,
        matchup_model: Optional[matchup.MatchupModel],
        vegas_model: Optional[vegas.VegasModel],
        wind_model: Optional[weather.WindModel],
    ) -> Optional[float]:
        """Your opponent's best-case lineup total, under the same signals
        applied to your own roster. None if there's no game (a bye week in
        the league) or the opponent's roster can't be read."""
        opponent_id = self._find_opponent_team_id(payload, team_id, week)
        if opponent_id is None:
            return None
        opponent_team = next(
            (t for t in payload.get("teams") or [] if t.get("id") == opponent_id), None
        )
        if opponent_team is None:
            return None
        opp_roster = parse_roster(opponent_team, week)
        if not opp_roster.players:
            return None

        availability.apply(opp_roster.players, week_schedule, respect_locks=False)
        if matchup_model is not None:
            matchup.apply(opp_roster.players, matchup_model)
        if vegas_model is not None:
            vegas.apply(opp_roster.players, vegas_model)
        if wind_model is not None:
            weather.apply(opp_roster.players, wind_model, week_schedule)

        return optimize(opp_roster, slot_counts).projected_total

    # ------------------------------------------------------------- decision

    def decide(
        self,
        week: Optional[int] = None,
        now: Optional[datetime] = None,
        forced_start: Optional[List[int]] = None,
        forced_bench: Optional[List[int]] = None,
    ) -> Decision:
        """Decide this week's lineup.

        ``forced_start``/``forced_bench`` are player ids from a parsed reply
        (see :mod:`fantasyagent.commands`); they override the normal ranking
        for those specific players.
        """
        settings = self.client.league(["mSettings"])
        week = week or settings.get("scoringPeriodId")
        if not week:
            raise ESPNError("ESPN did not report a current scoring period for this league.")

        slot_counts = starting_slot_counts(settings)
        if not slot_counts:
            raise ESPNError("Could not read this league's starting-lineup slots.")

        payload = self.client.league(["mRoster", "mTeam", "mMatchupScore"], scoring_period=week)
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

        # Matchup and volatility both need full season stat history; fetch it
        # once and skip entirely if both are off.
        raw_players: List[Dict[str, Any]] = []
        if self.config.use_matchup or self.config.use_volatility:
            try:
                raw_players = self._fetch_raw_players()
            except ESPNError as exc:
                log.warning("Player stat history unavailable (%s); skipping matchup/volatility", exc)

        matchup_model = None
        if self.config.use_matchup:
            matchup_model = matchup.build(
                raw_players, season_opponents(schedules, week), week, alpha=self.config.matchup_alpha
            )
            matchup.apply(roster.players, matchup_model)

        vegas_model = None
        if self.config.use_vegas:
            vegas_model = vegas.fetch(week, self.config.season)
            vegas.apply(roster.players, vegas_model)

        wind_model = None
        if self.config.use_weather and not week_schedule.is_empty():
            wind_model = weather.fetch(week_schedule, week_schedule.all_host_ids())
            weather.apply(roster.players, wind_model, week_schedule)

        current_total = sum(p.score for p in roster.players if p.is_starting)

        if self.config.use_volatility:
            baseline_total = optimize(roster, slot_counts).projected_total
            opponent_total = self._opponent_projected_total(
                payload, roster.team_id, week, slot_counts, week_schedule,
                matchup_model, vegas_model, wind_model,
            )
            if opponent_total is not None:
                margin = baseline_total - opponent_total
                volatility_model = volatility.build(raw_players, week)
                volatility.apply(roster.players, volatility_model, margin)
                log.info(
                    "Projected margin %+.1f vs opponent; volatility tilt %s",
                    margin,
                    "toward ceiling" if margin < 0 else "toward floor" if margin > 0 else "neutral",
                )

        for player in roster.players:
            if forced_start and player.player_id in forced_start:
                player.forced_start = True
            if forced_bench and player.player_id in forced_bench:
                player.forced_bench = True

        lineup = optimize(roster, slot_counts)
        return Decision(
            week=week, roster=roster, lineup=lineup,
            current_total=current_total, slot_counts=slot_counts,
        )

    def reoptimize(self, decision: Decision) -> Decision:
        """Re-run the optimizer over ``decision.roster`` after its players'
        forced_start/forced_bench flags were mutated (e.g. by an email reply)."""
        lineup = optimize(decision.roster, decision.slot_counts)
        return Decision(
            week=decision.week, roster=decision.roster, lineup=lineup,
            current_total=decision.current_total, slot_counts=decision.slot_counts,
        )

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
