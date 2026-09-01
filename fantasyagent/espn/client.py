"""Thin HTTP client for ESPN's v3 fantasy API.

Reads go to lm-api-reads, writes to lm-api-writes. Private leagues need the
SWID and espn_s2 cookies; public leagues work unauthenticated.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

import requests

log = logging.getLogger(__name__)

READ_HOST = "https://lm-api-reads.fantasy.espn.com"
WRITE_HOST = "https://lm-api-writes.fantasy.espn.com"


class ESPNError(RuntimeError):
    """An ESPN request failed in a way the agent cannot recover from."""


class ESPNClient:
    def __init__(
        self,
        league_id: int,
        season: int,
        swid: Optional[str] = None,
        espn_s2: Optional[str] = None,
        timeout: int = 30,
        max_retries: int = 3,
    ) -> None:
        self.league_id = league_id
        self.season = season
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update(
            {
                # ESPN rejects requests without a browser-ish UA.
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                ),
                "Accept": "application/json",
                "X-Fantasy-Source": "kona",
                "X-Fantasy-Platform": "kona-PROD--web",
            }
        )
        if swid and espn_s2:
            self.session.cookies.set("SWID", swid)
            self.session.cookies.set("espn_s2", espn_s2)
        self._swid = swid

    # ------------------------------------------------------------------ HTTP

    @property
    def _league_path(self) -> str:
        return f"/apis/v3/games/ffl/seasons/{self.season}/segments/0/leagues/{self.league_id}"

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                resp = self.session.request(
                    method,
                    url,
                    params=params,
                    headers=headers,
                    json=json_body,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:  # network flake
                last_exc = exc
                log.warning("ESPN %s %s failed (%s), retrying", method, url, exc)
                time.sleep(2**attempt)
                continue

            if resp.status_code in (401, 403):
                raise ESPNError(
                    f"ESPN returned {resp.status_code} for {url}. Your SWID/espn_s2 "
                    "cookies are missing, expired, or belong to an account that "
                    "cannot see this league."
                )
            if resp.status_code == 404:
                raise ESPNError(f"ESPN returned 404 for {url}. Check league_id and season.")
            if resp.status_code >= 500:
                last_exc = ESPNError(f"ESPN {resp.status_code}: {resp.text[:200]}")
                time.sleep(2**attempt)
                continue
            if not resp.ok:
                raise ESPNError(f"ESPN {resp.status_code} for {url}: {resp.text[:500]}")

            try:
                return resp.json()
            except ValueError as exc:
                raise ESPNError(f"ESPN returned non-JSON for {url}: {resp.text[:200]}") from exc

        raise ESPNError(f"ESPN {method} {url} failed after {self.max_retries} attempts: {last_exc}")

    # ------------------------------------------------------------------ reads

    def league(
        self,
        views: List[str],
        *,
        scoring_period: Optional[int] = None,
        fantasy_filter: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Fetch the league document with the given ``view`` projections."""
        params: Dict[str, Any] = {"view": views}
        if scoring_period is not None:
            params["scoringPeriodId"] = scoring_period
        headers = {}
        if fantasy_filter:
            headers["X-Fantasy-Filter"] = json.dumps(fantasy_filter)
        return self._request("GET", READ_HOST + self._league_path, params=params, headers=headers)

    def pro_team_schedules(self) -> Dict[str, Any]:
        """Season-wide NFL schedule, including bye weeks and kickoff times."""
        url = f"{READ_HOST}/apis/v3/games/ffl/seasons/{self.season}"
        return self._request("GET", url, params={"view": "proTeamSchedules_wl"})

    # ----------------------------------------------------------------- writes

    def submit_lineup(
        self,
        team_id: int,
        scoring_period: int,
        moves: List[Dict[str, int]],
    ) -> Dict[str, Any]:
        """Submit lineup moves as a single ROSTER transaction.

        ESPN evaluates the items as a set, so a swap can be expressed as two
        independent moves without the intermediate state being rejected.
        """
        if not self._swid:
            raise ESPNError("Setting a lineup requires ESPN_SWID and ESPN_S2 to be set.")
        body = {
            "isLeagueManager": False,
            "teamId": team_id,
            "type": "ROSTER",
            "memberId": self._swid,
            "scoringPeriodId": scoring_period,
            "executionType": "EXECUTE",
            "items": [
                {
                    "playerId": m["player_id"],
                    "type": "LINEUP",
                    "fromLineupSlotId": m["from_slot"],
                    "toLineupSlotId": m["to_slot"],
                }
                for m in moves
            ],
        }
        url = f"{WRITE_HOST}{self._league_path}/transactions/"
        return self._request("POST", url, json_body=body)
