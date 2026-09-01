"""Wind-driven downgrade for outdoor games, from Open-Meteo (free, no key).

Deliberately narrow: only wind, only outdoor stadiums, only at real severity.
Retractable roofs are treated like domes rather than tracked live — teams
close them exactly when weather would otherwise matter, so "outdoor-only"
is a reasonable proxy without needing a live roof-status feed. Rain alone is
skipped; it gets overrated relative to its actual scoring impact, and DST
isn't adjusted at all — turnovers from bad weather roughly wash out against
the missed field goals and time of possession it also costs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, Optional

import requests

from ..espn.models import Player
from .schedule import WeekSchedule
from .stadiums import STADIUMS

log = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

#: Below this, wind is not worth modeling.
CALM_MPH = 15.0
#: Above this, deep passing and kicking are meaningfully compromised.
SEVERE_MPH = 25.0

#: Positions whose fantasy value leans on the passing/kicking game.
WIND_SENSITIVE = {"QB", "WR", "TE", "K"}


@dataclass
class WindModel:
    #: pro_team_id (home team hosting the game) -> forecast wind speed, mph
    wind_mph: Dict[int, float]

    def is_empty(self) -> bool:
        return not self.wind_mph

    def multiplier(self, host_team_id: Optional[int]) -> float:
        if host_team_id is None:
            return 1.0
        mph = self.wind_mph.get(host_team_id)
        if mph is None or mph <= CALM_MPH:
            return 1.0
        if mph >= SEVERE_MPH:
            return 0.85
        # Linear taper between calm and severe.
        fraction = (mph - CALM_MPH) / (SEVERE_MPH - CALM_MPH)
        return 1.0 - 0.15 * fraction


def _fetch_wind_mph(lat: float, lon: float, when: datetime, timeout: int = 15) -> Optional[float]:
    try:
        resp = requests.get(
            FORECAST_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "hourly": "windspeed_10m",
                "windspeed_unit": "mph",
                "timezone": "UTC",
                "start_date": when.date().isoformat(),
                "end_date": when.date().isoformat(),
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        log.debug("Weather lookup failed for (%s, %s): %s", lat, lon, exc)
        return None

    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    speeds = hourly.get("windspeed_10m") or []
    if not times or not speeds:
        return None

    target = when.strftime("%Y-%m-%dT%H:00")
    if target in times:
        return speeds[times.index(target)]
    return None


def fetch(week_schedule: WeekSchedule, host_team_ids: Iterable[int]) -> WindModel:
    """Forecast wind for each outdoor game this week, keyed by the home team.

    Never raises: a lookup failure for one game just leaves that game out of
    the model, and a total outage returns an empty model (all no-ops).
    """
    wind: Dict[int, float] = {}
    for team_id in set(host_team_ids):
        stadium = STADIUMS.get(team_id)
        if stadium is None or stadium.roof != "outdoor":
            continue
        game = week_schedule.game_for(team_id)
        if game is None or game.home_team_id != team_id:
            continue
        mph = _fetch_wind_mph(stadium.lat, stadium.lon, game.kickoff)
        if mph is not None:
            wind[team_id] = mph
    return WindModel(wind)


def apply(players: Iterable[Player], model: WindModel, week_schedule: WeekSchedule) -> None:
    if model.is_empty():
        return
    for player in players:
        if player.position not in WIND_SENSITIVE:
            continue
        if player.exclusion_reason or player.forced_bench:
            continue
        host_id = week_schedule.host_team_id(player.pro_team_id)
        if host_id is None:
            continue
        mph = model.wind_mph.get(host_id)
        factor = model.multiplier(host_id)
        player.matchup_multiplier *= factor
        if mph is not None and factor < 1.0:
            player.reasons.append(f"{mph:.0f}mph wind")
