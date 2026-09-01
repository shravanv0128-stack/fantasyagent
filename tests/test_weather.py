from datetime import datetime, timezone

import requests

from fantasyagent.espn.constants import SLOT_BENCH, SLOT_QB, SLOT_RB
from fantasyagent.espn.models import Player
from fantasyagent.signals import weather
from fantasyagent.signals.schedule import WeekSchedule

# Team 2 (outdoor, per stadiums.py) hosts team 3 (dome team, doesn't matter —
# only the host's roof matters). Team 4 hosts in a dome.
KICKOFF = int(datetime(2026, 9, 13, 17, 0, tzinfo=timezone.utc).timestamp() * 1000)
SCHEDULE_PAYLOAD = {
    "settings": {
        "proTeams": [
            {"id": 2, "proGamesByScoringPeriod": {  # BUF, outdoor
                "2": [{"homeProTeamId": 2, "awayProTeamId": 3, "date": KICKOFF}]
            }},
            {"id": 3, "proGamesByScoringPeriod": {
                "2": [{"homeProTeamId": 2, "awayProTeamId": 3, "date": KICKOFF}]
            }},
            {"id": 8, "proGamesByScoringPeriod": {  # DET, dome
                "2": [{"homeProTeamId": 8, "awayProTeamId": 9, "date": KICKOFF}]
            }},
            {"id": 9, "proGamesByScoringPeriod": {
                "2": [{"homeProTeamId": 8, "awayProTeamId": 9, "date": KICKOFF}]
            }},
        ]
    }
}


def make_player(pid, position, pro_team_id, opponent_id):
    return Player(
        player_id=pid, name=f"P{pid}", position=position, pro_team_id=pro_team_id,
        eligible_slots=[SLOT_QB, SLOT_RB, SLOT_BENCH], current_slot=SLOT_BENCH,
        injury_status="ACTIVE", projected_points=10.0, opponent_id=opponent_id,
    )


def test_calm_wind_is_a_noop():
    model = weather.WindModel({2: 8.0})
    assert model.multiplier(2) == 1.0


def test_severe_wind_hits_the_floor():
    model = weather.WindModel({2: 40.0})
    assert model.multiplier(2) == 0.85


def test_wind_tapers_linearly_between_thresholds():
    model = weather.WindModel({2: 20.0})  # halfway between 15 and 25
    mult = model.multiplier(2)
    assert 0.85 < mult < 1.0


def test_unknown_host_is_neutral():
    model = weather.WindModel({2: 40.0})
    assert model.multiplier(99) == 1.0
    assert model.multiplier(None) == 1.0


def test_fetch_skips_dome_and_unlisted_teams(monkeypatch):
    calls = []

    def fake_get(url, params, timeout):
        calls.append(params)
        class R:
            def raise_for_status(self):
                pass
            def json(self):
                return {"hourly": {"time": [], "windspeed_10m": []}}
        return R()

    monkeypatch.setattr(weather.requests, "get", fake_get)
    schedule = WeekSchedule.from_espn(SCHEDULE_PAYLOAD, 2)
    weather.fetch(schedule, schedule.all_host_ids())
    # Only team 2 (BUF, outdoor) should trigger a lookup; team 8 (DET, dome) should not.
    assert len(calls) == 1


def test_fetch_matches_forecast_hour_to_kickoff(monkeypatch):
    def fake_get(url, params, timeout):
        class R:
            def raise_for_status(self):
                pass
            def json(self):
                return {
                    "hourly": {
                        "time": ["2026-09-13T16:00", "2026-09-13T17:00", "2026-09-13T18:00"],
                        "windspeed_10m": [10.0, 22.0, 12.0],
                    }
                }
        return R()

    monkeypatch.setattr(weather.requests, "get", fake_get)
    schedule = WeekSchedule.from_espn(SCHEDULE_PAYLOAD, 2)
    model = weather.fetch(schedule, schedule.all_host_ids())
    assert model.wind_mph[2] == 22.0  # matched the 17:00 kickoff hour, not 16:00/18:00


def test_fetch_degrades_on_network_failure(monkeypatch):
    def raise_error(*a, **kw):
        raise requests.RequestException("boom")

    monkeypatch.setattr(weather.requests, "get", raise_error)
    schedule = WeekSchedule.from_espn(SCHEDULE_PAYLOAD, 2)
    model = weather.fetch(schedule, schedule.all_host_ids())
    assert model.is_empty()


def test_apply_only_affects_wind_sensitive_positions_in_outdoor_games():
    schedule = WeekSchedule.from_espn(SCHEDULE_PAYLOAD, 2)
    model = weather.WindModel({2: 40.0})  # severe wind at BUF's game

    qb_outdoor = make_player(1, "QB", pro_team_id=2, opponent_id=3)
    rb_outdoor = make_player(2, "RB", pro_team_id=2, opponent_id=3)  # RB unaffected by design
    qb_dome = make_player(3, "QB", pro_team_id=8, opponent_id=9)     # dome game, unaffected

    weather.apply([qb_outdoor, rb_outdoor, qb_dome], model, schedule)
    assert qb_outdoor.matchup_multiplier == 0.85
    assert rb_outdoor.matchup_multiplier == 1.0
    assert qb_dome.matchup_multiplier == 1.0


def test_apply_skips_excluded_players():
    schedule = WeekSchedule.from_espn(SCHEDULE_PAYLOAD, 2)
    model = weather.WindModel({2: 40.0})
    qb = make_player(1, "QB", pro_team_id=2, opponent_id=3)
    qb.exclusion_reason = "out"
    weather.apply([qb], model, schedule)
    assert qb.matchup_multiplier == 1.0


def test_empty_model_is_a_full_noop():
    schedule = WeekSchedule.from_espn(SCHEDULE_PAYLOAD, 2)
    model = weather.WindModel({})
    qb = make_player(1, "QB", pro_team_id=2, opponent_id=3)
    weather.apply([qb], model, schedule)
    assert qb.matchup_multiplier == 1.0
