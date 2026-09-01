import requests

from fantasyagent.espn.constants import SLOT_BENCH, SLOT_DST, SLOT_RB, SLOT_WR
from fantasyagent.espn.models import Player
from fantasyagent.signals import vegas


def make_player(pid, position, pro_team_id, opponent_id=None, points=10.0):
    return Player(
        player_id=pid, name=f"P{pid}", position=position, pro_team_id=pro_team_id,
        eligible_slots=[SLOT_RB, SLOT_WR, SLOT_DST, SLOT_BENCH], current_slot=SLOT_BENCH,
        injury_status="ACTIVE", projected_points=points, opponent_id=opponent_id,
    )


SCOREBOARD_PAYLOAD = {
    "events": [
        {
            "competitions": [
                {
                    "competitors": [
                        {"homeAway": "home", "team": {"abbreviation": "KC"}},
                        {"homeAway": "away", "team": {"abbreviation": "DEN"}},
                    ],
                    "odds": [{"overUnder": 48.0, "spread": -7.0}],  # KC favored by 7
                }
            ]
        },
        {
            "competitions": [
                {
                    "competitors": [
                        {"homeAway": "home", "team": {"abbreviation": "NYJ"}},
                        {"homeAway": "away", "team": {"abbreviation": "NE"}},
                    ],
                    "odds": [{"overUnder": 37.0, "spread": 2.0}],  # NYJ underdog by 2
                }
            ]
        },
        {
            "competitions": [
                {
                    "competitors": [
                        {"homeAway": "home", "team": {"abbreviation": "SF"}},
                        {"homeAway": "away", "team": {"abbreviation": "SEA"}},
                    ],
                    "odds": [{"overUnder": 44.0, "spread": -3.0}],
                }
            ]
        },
        {
            "competitions": [
                {
                    "competitors": [
                        {"homeAway": "home", "team": {"abbreviation": "DAL"}},
                        {"homeAway": "away", "team": {"abbreviation": "PHI"}},
                    ],
                    "odds": [{"overUnder": 45.0, "spread": -1.0}],
                }
            ]
        },
    ]
}


def test_implied_totals_computed_from_spread_and_total():
    totals = {}
    for event in SCOREBOARD_PAYLOAD["events"]:
        comp = event["competitions"][0]
        home = next(c for c in comp["competitors"] if c["homeAway"] == "home")
        away = next(c for c in comp["competitors"] if c["homeAway"] == "away")
        totals.update(vegas._implied_totals_from_odds(
            comp["odds"][0], home["team"]["abbreviation"], away["team"]["abbreviation"]
        ))
    assert totals["KC"] == 27.5   # 48/2 + 7/2
    assert totals["DEN"] == 20.5  # 48/2 - 7/2
    assert totals["NYJ"] == 17.5  # 37/2 - 2/2 (home underdog)
    assert totals["NE"] == 19.5   # 37/2 + 2/2


def test_fetch_parses_a_full_scoreboard(monkeypatch):
    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return SCOREBOARD_PAYLOAD

    monkeypatch.setattr(vegas.requests, "get", lambda *a, **kw: FakeResp())
    model = vegas.fetch(week=3, season=2026)
    assert not model.is_empty()
    assert model.implied_total("KC") == 27.5


def test_fetch_degrades_on_network_failure(monkeypatch):
    def raise_error(*a, **kw):
        raise requests.RequestException("boom")

    monkeypatch.setattr(vegas.requests, "get", raise_error)
    model = vegas.fetch(week=3, season=2026)
    assert model.is_empty()


def test_fetch_degrades_on_bad_json(monkeypatch):
    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            raise ValueError("not json")

    monkeypatch.setattr(vegas.requests, "get", lambda *a, **kw: FakeResp())
    assert vegas.fetch(week=3, season=2026).is_empty()


def test_offense_multiplier_rewards_high_implied_total():
    model = vegas.VegasModel({"KC": 30.0, "DEN": 17.0, "SF": 24.0, "SEA": 20.0})
    assert model.offense_multiplier("KC") > 1.0
    assert model.offense_multiplier("DEN") < 1.0
    assert vegas.CLIP[0] <= model.offense_multiplier("DEN") <= vegas.CLIP[1]


def test_offense_multiplier_neutral_for_unknown_team():
    model = vegas.VegasModel({"KC": 30.0, "DEN": 17.0, "SF": 24.0, "SEA": 20.0})
    assert model.offense_multiplier("ZZZ") == 1.0
    assert model.offense_multiplier(None) == 1.0


def test_defense_multiplier_is_inverse_of_opponent_total():
    model = vegas.VegasModel({"KC": 30.0, "DEN": 14.0, "SF": 24.0, "SEA": 20.0})
    # A defense facing a low-implied-total offense (DEN, 14) should be boosted.
    assert model.defense_multiplier("DEN") > 1.0
    assert model.defense_multiplier("KC") < 1.0


def test_apply_skips_excluded_and_forced_bench_players():
    model = vegas.VegasModel({"KC": 30.0, "DEN": 14.0, "SF": 24.0, "SEA": 20.0})
    excluded = make_player(1, "RB", pro_team_id=1)
    excluded.exclusion_reason = "out"
    excluded.pro_team_id = 12  # not in model at all; irrelevant since excluded anyway
    starter = make_player(2, "WR", pro_team_id=12)
    vegas.apply([excluded, starter], model)
    assert excluded.matchup_multiplier == 1.0


def test_apply_uses_opponent_total_for_dst():
    from fantasyagent.espn.constants import PRO_TEAM_ABBREV
    kc_id = next(k for k, v in PRO_TEAM_ABBREV.items() if v == "KC")
    den_id = next(k for k, v in PRO_TEAM_ABBREV.items() if v == "DEN")
    model = vegas.VegasModel({"KC": 30.0, "DEN": 14.0, "SF": 24.0, "SEA": 20.0})
    dst = make_player(1, "D/ST", pro_team_id=den_id, opponent_id=kc_id)
    vegas.apply([dst], model)
    assert dst.matchup_multiplier < 1.0  # facing a high-implied-total offense


def test_empty_model_is_a_full_noop():
    model = vegas.VegasModel({})
    p = make_player(1, "WR", pro_team_id=12)
    before = p.matchup_multiplier
    vegas.apply([p], model)
    assert p.matchup_multiplier == before
