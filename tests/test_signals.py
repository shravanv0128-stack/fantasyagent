from datetime import datetime, timedelta, timezone

from fantasyagent.espn.constants import SLOT_BENCH, SLOT_RB
from fantasyagent.espn.models import Player
from fantasyagent.signals import availability, matchup
from fantasyagent.signals.schedule import WeekSchedule, season_opponents

NOW = datetime(2026, 9, 13, 16, 0, tzinfo=timezone.utc)

# Team 1 hosts team 2 at 17:00Z; team 3 has no game (bye).
SCHEDULE_PAYLOAD = {
    "settings": {
        "proTeams": [
            {
                "id": 1,
                "proGamesByScoringPeriod": {
                    "2": [{"homeProTeamId": 1, "awayProTeamId": 2,
                           "date": int(datetime(2026, 9, 13, 17, 0, tzinfo=timezone.utc).timestamp() * 1000)}],
                },
            },
            {
                "id": 2,
                "proGamesByScoringPeriod": {
                    "2": [{"homeProTeamId": 1, "awayProTeamId": 2,
                           "date": int(datetime(2026, 9, 13, 15, 0, tzinfo=timezone.utc).timestamp() * 1000)}],
                },
            },
            {"id": 3, "proGamesByScoringPeriod": {"1": [{"homeProTeamId": 3, "awayProTeamId": 1, "date": 0}]}},
        ]
    }
}


def player(pid, pro_team_id, status="ACTIVE", points=10.0):
    return Player(
        player_id=pid, name=f"P{pid}", position="RB", pro_team_id=pro_team_id,
        eligible_slots=[SLOT_RB, SLOT_BENCH], current_slot=SLOT_BENCH,
        injury_status=status, projected_points=points,
    )


def test_bye_week_is_excluded():
    schedule = WeekSchedule.from_espn(SCHEDULE_PAYLOAD, 2)
    p = player(1, pro_team_id=3)
    availability.apply([p], schedule, now=NOW)
    assert p.on_bye and p.exclusion_reason == "on bye" and p.score == 0.0


def test_out_and_doubtful_are_excluded_questionable_is_discounted():
    schedule = WeekSchedule.from_espn(SCHEDULE_PAYLOAD, 2)
    out, doubtful, questionable = player(1, 1, "OUT"), player(2, 1, "DOUBTFUL"), player(3, 1, "QUESTIONABLE")
    availability.apply([out, doubtful, questionable], schedule, now=NOW)
    assert out.score == 0.0 and doubtful.score == 0.0
    assert questionable.score == 8.0  # 10.0 * 0.80, still startable


def test_kickoff_locks_a_player_without_excluding_them():
    schedule = WeekSchedule.from_espn(SCHEDULE_PAYLOAD, 2)
    started, upcoming = player(1, 2), player(2, 1)  # team 2 kicked off at 15:00Z
    availability.apply([started, upcoming], schedule, now=NOW)
    assert started.game_started and started.exclusion_reason is None
    assert not upcoming.game_started


def test_locks_can_be_disabled():
    schedule = WeekSchedule.from_espn(SCHEDULE_PAYLOAD, 2)
    p = player(1, 2)
    availability.apply([p], schedule, now=NOW, respect_locks=False)
    assert not p.game_started


def test_missing_schedule_does_not_bench_the_whole_roster():
    p = player(1, 3)
    availability.apply([p], WeekSchedule({}), now=NOW)
    assert p.exclusion_reason is None


def test_opponent_is_recorded():
    p = player(1, 1)
    availability.apply([p], WeekSchedule.from_espn(SCHEDULE_PAYLOAD, 2), now=NOW)
    assert p.opponent_id == 2


def test_season_opponents_stops_at_requested_week():
    opponents = season_opponents(SCHEDULE_PAYLOAD, through_week=1)
    assert opponents[3] == {1: 1}
    assert opponents[1] == {}


# --------------------------------------------------------------- matchup


def _stat(week, points, source=0):
    return {"scoringPeriodId": week, "statSourceId": source, "statSplitTypeId": 1,
            "appliedTotal": points}


def _build_model(points_by_defense, weeks=4):
    """One RB per offense; each offense faces exactly one defense every week."""
    players, opponents = [], {}
    for i, (defense, points) in enumerate(points_by_defense.items()):
        offense = 100 + i
        players.append({
            "id": offense, "defaultPositionId": 2, "proTeamId": offense,
            "stats": [_stat(w, points) for w in range(1, weeks + 1)],
        })
        opponents[offense] = {w: defense for w in range(1, weeks + 1)}
    return matchup.build(players, opponents, through_week=weeks + 1)


def test_soft_defenses_get_a_bonus_and_tough_ones_a_penalty():
    model = _build_model({d: float(d) for d in range(1, 13)})  # allows 1..12 points
    assert model.multiplier("RB", 12) > 1.0
    assert model.multiplier("RB", 1) < 1.0
    assert model.multiplier("RB", 12) <= 1.12
    assert model.multiplier("RB", 1) >= 0.88


def test_multiplier_is_neutral_for_unknown_defenses_and_thin_samples():
    model = _build_model({d: float(d) for d in range(1, 13)})
    assert model.multiplier("RB", 99) == 1.0
    assert model.multiplier("QB", 1) == 1.0  # no QB data at all
    assert model.multiplier("RB", None) == 1.0

    thin = _build_model({d: float(d) for d in range(1, 4)})  # only 3 defenses
    assert thin.is_empty()
    assert thin.multiplier("RB", 1) == 1.0


def test_matchup_never_overrides_an_exclusion():
    model = _build_model({d: float(d) for d in range(1, 13)})
    p = player(1, 1)
    p.exclusion_reason = "out"
    p.opponent_id = 12
    matchup.apply([p], model)
    assert p.score == 0.0
