"""Integration test: matchup, vegas, weather, and opponent-aware volatility
all wired together through LineupAgent.decide(), each mocked at its own
network boundary so nothing here touches a real HTTP request.
"""

from datetime import datetime, timezone

import pytest

from fantasyagent.agent import LineupAgent
from fantasyagent.config import Config
from fantasyagent.espn.client import ESPNError
from fantasyagent.espn.constants import (
    SLOT_BENCH, SLOT_DST, SLOT_FLEX, SLOT_K, SLOT_QB, SLOT_RB, SLOT_TE, SLOT_WR,
)
from fantasyagent.signals import vegas, weather

WEEK = 4
KICKOFF = int(datetime(2026, 9, 20, 17, 0, tzinfo=timezone.utc).timestamp() * 1000)
BEFORE_KICKOFF = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)

SETTINGS = {
    "scoringPeriodId": WEEK,
    "settings": {
        "rosterSettings": {
            "lineupSlotCounts": {
                "0": 1, "2": 2, "4": 2, "6": 1, "23": 1, "16": 1, "17": 1,
                "20": 7, "21": 1,
            }
        }
    },
}

# Team 2 hosts team 1 (an outdoor stadium, per stadiums.py) so the weather
# signal has something to bite on; every other team gets a bye-avoiding game
# against team 33 as filler.
SCHEDULES = {
    "settings": {
        "proTeams": (
            [{"id": 2, "proGamesByScoringPeriod": {
                str(WEEK): [{"homeProTeamId": 2, "awayProTeamId": 1, "date": KICKOFF}]
            }}]
            + [{"id": 1, "proGamesByScoringPeriod": {
                str(WEEK): [{"homeProTeamId": 2, "awayProTeamId": 1, "date": KICKOFF}]
            }}]
            + [
                {"id": t, "proGamesByScoringPeriod": {
                    str(WEEK): [{"homeProTeamId": t, "awayProTeamId": 33, "date": KICKOFF}]
                }}
                for t in range(3, 13)
            ]
        )
    }
}

ELIGIBLE_BY_POSITION = {
    1: [SLOT_QB, SLOT_BENCH],
    2: [SLOT_RB, SLOT_FLEX, SLOT_BENCH],
    3: [SLOT_WR, SLOT_FLEX, SLOT_BENCH],
    4: [SLOT_TE, SLOT_FLEX, SLOT_BENCH],
    5: [SLOT_K, SLOT_BENCH],
    16: [SLOT_DST, SLOT_BENCH],
}


def _entry(pid, name, pos, team, slot, projection, injury="ACTIVE", history=None):
    stats = [{"scoringPeriodId": WEEK, "statSourceId": 1, "statSplitTypeId": 1, "appliedTotal": projection}]
    for week, points in (history or {}).items():
        stats.append({"scoringPeriodId": week, "statSourceId": 0, "statSplitTypeId": 1, "appliedTotal": points})
    return {
        "lineupSlotId": slot,
        "playerPoolEntry": {"player": {
            "id": pid, "fullName": name, "defaultPositionId": pos,
            "proTeamId": team, "injuryStatus": injury,
            "eligibleSlots": ELIGIBLE_BY_POSITION[pos],
            "stats": stats,
        }},
    }


MY_ROSTER = [
    _entry(1, "QB1", 1, 2, SLOT_QB, 20.0, history={1: 18, 2: 19}),
    _entry(2, "RB1", 2, 3, SLOT_RB, 14.0, history={1: 4, 2: 22, 3: 3}),  # boom/bust
    _entry(3, "RB2", 2, 4, SLOT_RB, 12.0, history={1: 12, 2: 11, 3: 12}),  # steady
    _entry(4, "WR1", 3, 5, SLOT_WR, 15.0, history={1: 15, 2: 14}),
    _entry(5, "WR2", 3, 6, SLOT_WR, 13.0, history={1: 13, 2: 12}),
    _entry(6, "TE1", 4, 7, SLOT_TE, 9.0, history={1: 9, 2: 8}),
    _entry(7, "K1", 5, 9, SLOT_K, 8.0, history={1: 8, 2: 7}),
    _entry(8, "DST1", 16, 10, SLOT_DST, 7.0, history={1: 7, 2: 6}),
    _entry(9, "FLEX1", 2, 11, SLOT_FLEX, 10.0, history={1: 10, 2: 9}),
]

# A weak opponent roster, so "my team" projects as a big favorite.
OPPONENT_ROSTER = [
    _entry(101, "OppQB", 1, 12, SLOT_QB, 8.0),
    _entry(102, "OppRB1", 2, 3, SLOT_RB, 5.0),
    _entry(103, "OppRB2", 2, 4, SLOT_RB, 4.0),
    _entry(104, "OppWR1", 3, 5, SLOT_WR, 5.0),
    _entry(105, "OppWR2", 3, 6, SLOT_WR, 4.0),
    _entry(106, "OppTE", 4, 7, SLOT_TE, 3.0),
    _entry(107, "OppK", 5, 9, SLOT_K, 3.0),
    _entry(108, "OppDST", 16, 10, SLOT_DST, 2.0),
    _entry(109, "OppFLEX", 2, 11, SLOT_FLEX, 3.0),
]

ROSTER_PAYLOAD = {
    "teams": [
        {"id": 7, "name": "My Team", "roster": {"entries": MY_ROSTER}},
        {"id": 8, "name": "Rival", "roster": {"entries": OPPONENT_ROSTER}},
    ],
    "schedule": [
        {"matchupPeriodId": WEEK, "home": {"teamId": 7}, "away": {"teamId": 8}},
    ],
}

def _game(home, away, over_under, spread):
    return {"competitions": [{
        "competitors": [
            {"homeAway": "home", "team": {"abbreviation": home}},
            {"homeAway": "away", "team": {"abbreviation": away}},
        ],
        "odds": [{"overUnder": over_under, "spread": spread}],
    }]}


# BUF/ATL are pro teams 2/1, the ones our roster actually uses; the rest are
# filler so the model has enough games for its z-score to be meaningful.
VEGAS_PAYLOAD = {
    "events": [
        _game("BUF", "ATL", 44.0, -6.0),   # BUF implied 25, well above average
        _game("KC", "DEN", 45.0, -9.0),
        _game("NYJ", "NE", 37.0, 2.0),
        _game("CHI", "GB", 41.0, 1.0),
    ]
}


class FullClient:
    def __init__(self):
        self.submitted = []

    def league(self, views, scoring_period=None, fantasy_filter=None):
        if "mSettings" in views:
            return SETTINGS
        if "mRoster" in views:
            return ROSTER_PAYLOAD
        if "kona_player_info" in views:
            all_players = [
                e["playerPoolEntry"]["player"] for e in MY_ROSTER + OPPONENT_ROSTER
            ]
            return {"players": all_players}
        raise AssertionError(f"unexpected views {views}")

    def pro_team_schedules(self):
        return SCHEDULES

    def submit_lineup(self, team_id, scoring_period, moves):
        self.submitted.append((team_id, scoring_period, moves))
        return {"status": "ok"}


def full_config(**overrides):
    base = dict(
        league_id=1, season=2026, team_id=7,
        use_matchup=True, use_vegas=True, use_weather=True, use_volatility=True,
    )
    base.update(overrides)
    return Config(**base)


@pytest.fixture
def mocked_http(monkeypatch):
    """vegas.requests and weather.requests are the SAME imported `requests`
    module (Python caches imports), so patching requests.get twice would
    have the second patch silently clobber the first. One dispatcher,
    routed by URL, avoids that trap."""

    class VegasResp:
        def raise_for_status(self):
            pass

        def json(self):
            return VEGAS_PAYLOAD

    class WeatherResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "hourly": {
                    "time": ["2026-09-20T17:00"],
                    "windspeed_10m": [30.0],  # severe, well past SEVERE_MPH
                }
            }

    def dispatch(url, *a, **kw):
        if url == vegas.SCOREBOARD_URL:
            return VegasResp()
        if url == weather.FORECAST_URL:
            return WeatherResp()
        raise AssertionError(f"unexpected request to {url}")

    monkeypatch.setattr(vegas.requests, "get", dispatch)


def test_all_signals_run_together_without_crashing(mocked_http):
    agent = LineupAgent(full_config(), client=FullClient())
    decision = agent.decide(now=BEFORE_KICKOFF)
    assert decision.lineup.projected_total > 0
    assert decision.roster.by_id(1) is not None


def test_vegas_signal_actually_moved_a_score(mocked_http):
    agent = LineupAgent(full_config(), client=FullClient())
    decision = agent.decide(now=BEFORE_KICKOFF)
    # BUF (pro team 2) is a 6-point favorite in a 44-total game -> implied 25,
    # a real edge over the road team's implied 19. QB1's own team is BUF.
    assert decision.roster.by_id(1).matchup_multiplier != 1.0


def test_weather_signal_downgraded_the_qb(mocked_http):
    agent = LineupAgent(full_config(), client=FullClient())
    decision = agent.decide(now=BEFORE_KICKOFF)
    # 30mph wind at BUF's (outdoor) stadium should be baked into the QB's total
    # multiplier as a genuine discount versus a calm-weather run.
    calm_config = full_config(use_weather=False)
    calm_decision = LineupAgent(calm_config, client=FullClient()).decide(now=BEFORE_KICKOFF)
    windy_qb = decision.roster.by_id(1)
    calm_qb = calm_decision.roster.by_id(1)
    assert windy_qb.score < calm_qb.score


def test_volatility_tilts_toward_ceiling_when_a_big_favorite(mocked_http):
    # My roster projects far ahead of the weak opponent roster, so the
    # boom/bust RB1 (history 4, 22) should get a *penalty* here — a big
    # favorite wants to reduce variance, not chase upside.
    agent = LineupAgent(full_config(), client=FullClient())
    decision = agent.decide(now=BEFORE_KICKOFF)
    volatile_rb = decision.roster.by_id(2)   # RB1, boom/bust
    steady_rb = decision.roster.by_id(3)     # RB2, steady
    assert volatile_rb.matchup_multiplier < steady_rb.matchup_multiplier


def test_opponent_lookup_failure_is_a_graceful_noop(mocked_http):
    """No schedule entry for this team/week -> volatility signal skips
    itself rather than raising."""
    class NoScheduleClient(FullClient):
        def league(self, views, scoring_period=None, fantasy_filter=None):
            payload = super().league(views, scoring_period, fantasy_filter)
            if "mRoster" in views:
                payload = dict(payload)
                payload["schedule"] = []
            return payload

    agent = LineupAgent(full_config(), client=NoScheduleClient())
    decision = agent.decide(now=BEFORE_KICKOFF)  # must not raise
    assert decision.lineup.projected_total > 0


def test_reasons_explain_why_each_signal_moved_the_score(mocked_http):
    agent = LineupAgent(full_config(), client=FullClient())
    decision = agent.decide(now=BEFORE_KICKOFF)
    qb = decision.roster.by_id(1)  # BUF QB: strong implied total + severe wind
    assert any("implied" in r for r in qb.reasons)
    assert any("wind" in r for r in qb.reasons)


def test_disabling_all_new_signals_matches_the_simple_baseline(mocked_http):
    plain_config = full_config(use_matchup=False, use_vegas=False, use_weather=False, use_volatility=False)
    decision = LineupAgent(plain_config, client=FullClient()).decide(now=BEFORE_KICKOFF)
    for player in decision.roster.players:
        assert player.matchup_multiplier == 1.0
