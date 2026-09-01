from datetime import datetime, timezone

import pytest

from fantasyagent.agent import LineupAgent
from fantasyagent.config import Config, ConfigError
from fantasyagent.espn.client import ESPNError
from fantasyagent.espn.constants import (
    SLOT_BENCH, SLOT_DST, SLOT_FLEX, SLOT_K, SLOT_QB, SLOT_RB, SLOT_TE, SLOT_WR,
)
from fantasyagent.espn.models import parse_roster, starting_slot_counts

WEEK = 3
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

SCHEDULES = {
    "settings": {
        "proTeams": [
            {"id": t, "proGamesByScoringPeriod": {
                str(WEEK): [{"homeProTeamId": t, "awayProTeamId": 33, "date": KICKOFF}]
            }}
            for t in range(1, 13)
        ]
        # Team 13 is absent from week 3 entirely -> on bye.
        + [{"id": 13, "proGamesByScoringPeriod": {"1": [{"homeProTeamId": 13, "awayProTeamId": 1, "date": 0}]}}]
    }
}

ROSTER_SPEC = [
    # id, name, positionId, proTeam, slot, projection, injury
    (1, "Star QB", 1, 1, SLOT_QB, 22.0, "ACTIVE"),
    (2, "Hurt RB", 2, 2, SLOT_RB, 19.0, "OUT"),
    (3, "Solid RB", 2, 3, SLOT_RB, 14.0, "ACTIVE"),
    (4, "Bye RB", 2, 13, SLOT_FLEX, 16.0, "ACTIVE"),
    (5, "Bench RB", 2, 4, SLOT_BENCH, 11.0, "ACTIVE"),
    (6, "WR One", 3, 5, SLOT_WR, 15.0, "ACTIVE"),
    (7, "WR Two", 3, 6, SLOT_WR, 12.0, "ACTIVE"),
    (8, "WR Three", 3, 7, SLOT_BENCH, 13.0, "ACTIVE"),
    (9, "The TE", 4, 8, SLOT_TE, 9.0, "ACTIVE"),
    (10, "Kicker", 5, 9, SLOT_K, 8.0, "ACTIVE"),
    (11, "Defense", 16, 10, SLOT_DST, 7.0, "ACTIVE"),
]

ELIGIBLE_BY_POSITION = {
    1: [SLOT_QB, SLOT_BENCH],
    2: [SLOT_RB, SLOT_FLEX, SLOT_BENCH],
    3: [SLOT_WR, SLOT_FLEX, SLOT_BENCH],
    4: [SLOT_TE, SLOT_FLEX, SLOT_BENCH],
    5: [SLOT_K, SLOT_BENCH],
    16: [SLOT_DST, SLOT_BENCH],
}


def _roster_payload():
    entries = []
    for pid, name, pos, team, slot, projection, injury in ROSTER_SPEC:
        entries.append({
            "lineupSlotId": slot,
            "playerPoolEntry": {"player": {
                "id": pid, "fullName": name, "defaultPositionId": pos,
                "proTeamId": team, "injuryStatus": injury,
                "eligibleSlots": ELIGIBLE_BY_POSITION[pos],
                "stats": [{"scoringPeriodId": WEEK, "statSourceId": 1,
                           "statSplitTypeId": 1, "appliedTotal": projection}],
            }},
        })
    return {"teams": [
        {"id": 7, "name": "My Team", "roster": {"entries": entries}},
        {"id": 8, "name": "Rival", "roster": {"entries": []}},
    ]}


class FakeClient:
    """Stands in for ESPNClient; records writes instead of performing them."""

    def __init__(self, fail_player_info=False):
        self.submitted = []
        self.fail_player_info = fail_player_info

    def league(self, views, scoring_period=None, fantasy_filter=None):
        if "mSettings" in views:
            return SETTINGS
        if "mRoster" in views:
            return _roster_payload()
        if "kona_player_info" in views:
            if self.fail_player_info:
                raise ESPNError("boom")
            return {"players": []}
        raise AssertionError(f"unexpected views {views}")

    def pro_team_schedules(self):
        return SCHEDULES

    def submit_lineup(self, team_id, scoring_period, moves):
        self.submitted.append((team_id, scoring_period, moves))
        return {"status": "ok"}


def make_config(**kwargs):
    base = dict(league_id=1, season=2026, team_id=7, use_matchup=False)
    base.update(kwargs)
    return Config(**base)


def test_settings_parse_into_starting_slots():
    assert starting_slot_counts(SETTINGS) == {
        SLOT_QB: 1, SLOT_RB: 2, SLOT_WR: 2, SLOT_TE: 1, SLOT_DST: 1, SLOT_K: 1, SLOT_FLEX: 1,
    }


def test_roster_parses_projection_injury_and_slot():
    roster = parse_roster(_roster_payload()["teams"][0], WEEK)
    assert roster.team_name == "My Team"
    hurt = roster.by_id(2)
    assert (hurt.position, hurt.injury_status, hurt.projected_points) == ("RB", "OUT", 19.0)
    assert roster.by_id(1).is_starting


def test_missing_projection_defaults_to_zero():
    payload = _roster_payload()
    payload["teams"][0]["roster"]["entries"][0]["playerPoolEntry"]["player"]["stats"] = []
    assert parse_roster(payload["teams"][0], WEEK).by_id(1).projected_points == 0.0


def test_decide_benches_injured_and_bye_players():
    agent = LineupAgent(make_config(), client=FakeClient())
    decision = agent.decide(now=BEFORE_KICKOFF)

    assert decision.week == WEEK
    assignments = decision.lineup.assignments
    assert assignments[2] == SLOT_BENCH, "OUT player must not start"
    assert assignments[4] == SLOT_BENCH, "bye-week player must not start"
    assert assignments[5] == SLOT_RB, "healthy bench RB gets promoted"
    assert assignments[8] == SLOT_FLEX, "best remaining flex option starts"
    assert decision.gain > 0


def test_decide_resolves_team_by_name():
    agent = LineupAgent(make_config(team_id=None, team_name="my team"), client=FakeClient())
    assert agent.decide(now=BEFORE_KICKOFF).roster.team_id == 7


def test_unknown_team_is_a_clear_error():
    agent = LineupAgent(make_config(team_id=99), client=FakeClient())
    with pytest.raises(ESPNError, match="No team with id 99"):
        agent.decide(now=BEFORE_KICKOFF)


def test_matchup_failure_degrades_instead_of_raising():
    agent = LineupAgent(make_config(use_matchup=True), client=FakeClient(fail_player_info=True))
    assert agent.decide(now=BEFORE_KICKOFF).lineup.has_changes


def test_submit_sends_every_move_in_one_transaction():
    client = FakeClient()
    agent = LineupAgent(make_config(), client=client)
    decision = agent.decide(now=BEFORE_KICKOFF)

    assert agent.submit(decision) is True
    team_id, week, moves = client.submitted[0]
    assert (team_id, week) == (7, WEEK)
    assert len(moves) == len(decision.lineup.moves)
    assert {"player_id", "from_slot", "to_slot"} == set(moves[0])
    # Every move names where the player is coming from, so ESPN can validate it.
    assert all(m["from_slot"] != m["to_slot"] for m in moves)


def test_dry_run_writes_nothing():
    client = FakeClient()
    agent = LineupAgent(make_config(), client=client)
    assert agent.submit(agent.decide(now=BEFORE_KICKOFF), dry_run=True) is False
    assert client.submitted == []


def test_config_requires_a_team_identifier():
    with pytest.raises(ConfigError):
        Config(league_id=1, season=2026)
