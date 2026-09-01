"""Game-environment adjustment from Vegas lines.

"Fantasy points allowed to position" is confounded by strength of schedule —
a defense that faced Ja'Marr Chase and Justin Jefferson looks generous to WRs
for reasons that have nothing to do with its actual coverage. The team's own
implied point total, from the spread and total, sidesteps that: it prices in
everything the market knows about both offenses and both defenses for this
specific game, refreshed all week as news breaks.

Source: ESPN's public scoreboard endpoint (site.api.espn.com), which needs no
authentication and carries the same odds ESPN shows on its own scoreboard.
This is a separate, unauthenticated ESPN service from the fantasy API the rest
of this package talks to.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

import requests

from ..espn.models import Player

log = logging.getLogger(__name__)

SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"

#: How far a team's implied total is allowed to move its players' scores.
#: Kept modest because Vegas lines already partly inform ESPN's own
#: projections; this is meant to sharpen close calls, not override them.
ALPHA = 0.05
CLIP = (0.92, 1.08)
#: D/ST is scored on the OPPONENT's implied total (low = good for D/ST), and
#: the sport's higher defensive variance earns it a slightly wider band.
DST_ALPHA = 0.07
DST_CLIP = (0.88, 1.12)


class VegasModel:
    """Implied point totals by NFL team abbreviation, e.g. ``{"KC": 27.5}``."""

    def __init__(self, implied_totals: Dict[str, float]) -> None:
        self._totals = implied_totals
        values = list(implied_totals.values())
        self._league_avg = statistics.fmean(values) if values else None
        self._stdev = statistics.pstdev(values) if len(values) >= 4 else None

    def is_empty(self) -> bool:
        return not self._totals

    def implied_total(self, team_abbrev: Optional[str]) -> Optional[float]:
        return self._totals.get(team_abbrev) if team_abbrev else None

    def offense_multiplier(self, team_abbrev: Optional[str]) -> float:
        return self._multiplier(team_abbrev, ALPHA, CLIP)

    def defense_multiplier(self, opponent_abbrev: Optional[str]) -> float:
        """Lower opponent implied total is better for a defense, so the
        z-score is inverted before scaling."""
        if opponent_abbrev is None or self._stdev is None:
            return 1.0
        total = self._totals.get(opponent_abbrev)
        if total is None:
            return 1.0
        z = (total - self._league_avg) / self._stdev
        low, high = DST_CLIP
        return max(low, min(high, 1.0 - DST_ALPHA * z))

    def _multiplier(self, team_abbrev, alpha, clip) -> float:
        if team_abbrev is None or self._stdev is None:
            return 1.0
        total = self._totals.get(team_abbrev)
        if total is None:
            return 1.0
        z = (total - self._league_avg) / self._stdev
        low, high = clip
        return max(low, min(high, 1.0 + alpha * z))


def _implied_totals_from_odds(odds: Dict[str, Any], home_abbrev: str, away_abbrev: str) -> Dict[str, float]:
    over_under = odds.get("overUnder")
    spread = odds.get("spread")  # ESPN convention: home team's spread, negative if home favored
    if over_under is None or spread is None:
        return {}
    home_implied = over_under / 2 - spread / 2
    away_implied = over_under / 2 + spread / 2
    return {home_abbrev: round(home_implied, 2), away_abbrev: round(away_implied, 2)}


def fetch(week: int, season: int, timeout: int = 20) -> VegasModel:
    """Pull this week's lines from ESPN's public scoreboard.

    Never raises: any problem (network, unexpected shape, no lines posted
    yet) returns an empty model, which makes every multiplier a no-op.
    """
    try:
        resp = requests.get(
            SCOREBOARD_URL,
            params={"week": week, "seasontype": 2, "year": season},
            timeout=timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("Vegas lines unavailable (%s); skipping game-environment adjustment", exc)
        return VegasModel({})

    totals: Dict[str, float] = {}
    for event in payload.get("events") or []:
        for competition in event.get("competitions") or []:
            odds_list = competition.get("odds") or []
            if not odds_list:
                continue
            competitors = competition.get("competitors") or []
            home = next((c for c in competitors if c.get("homeAway") == "home"), None)
            away = next((c for c in competitors if c.get("homeAway") == "away"), None)
            if not home or not away:
                continue
            home_abbrev = (home.get("team") or {}).get("abbreviation")
            away_abbrev = (away.get("team") or {}).get("abbreviation")
            if not home_abbrev or not away_abbrev:
                continue
            totals.update(_implied_totals_from_odds(odds_list[0], home_abbrev, away_abbrev))

    if not totals:
        log.info("No Vegas lines posted yet for week %s; skipping game-environment adjustment", week)
    return VegasModel(totals)


def apply(players: Iterable[Player], model: VegasModel) -> None:
    if model.is_empty():
        return
    for player in players:
        if player.exclusion_reason or player.forced_bench:
            continue
        if player.position == "D/ST":
            opponent_abbrev = _abbrev_for(player.opponent_id)
            factor = model.defense_multiplier(opponent_abbrev)
            player.matchup_multiplier *= factor
            total = model.implied_total(opponent_abbrev)
            if total is not None and factor >= 1.02:
                player.reasons.append(f"opponent implied for only {total:.1f}")
            elif total is not None and factor <= 0.98:
                player.reasons.append(f"opponent implied for {total:.1f}")
        else:
            own_abbrev = _abbrev_for(player.pro_team_id)
            factor = model.offense_multiplier(own_abbrev)
            player.matchup_multiplier *= factor
            total = model.implied_total(own_abbrev)
            if total is not None and factor >= 1.02:
                player.reasons.append(f"team implied for {total:.1f}")
            elif total is not None and factor <= 0.98:
                player.reasons.append(f"team implied for only {total:.1f}")


def _abbrev_for(pro_team_id: Optional[int]) -> Optional[str]:
    from ..espn.constants import PRO_TEAM_ABBREV

    if pro_team_id is None:
        return None
    return PRO_TEAM_ABBREV.get(pro_team_id)
