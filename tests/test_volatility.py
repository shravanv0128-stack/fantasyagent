from fantasyagent.espn.constants import SLOT_BENCH, SLOT_RB
from fantasyagent.espn.models import Player
from fantasyagent.signals import volatility


def make_player(pid, points=10.0):
    return Player(
        player_id=pid, name=f"P{pid}", position="RB", pro_team_id=1,
        eligible_slots=[SLOT_RB, SLOT_BENCH], current_slot=SLOT_BENCH,
        injury_status="ACTIVE", projected_points=points,
    )


def _stat(week, points):
    return {"scoringPeriodId": week, "statSourceId": 0, "statSplitTypeId": 1, "appliedTotal": points}


def test_build_computes_coefficient_of_variation():
    # Steady scorer: 10, 10, 10, 10 -> cv near 0. Boom/bust: 2, 18, 4, 16 -> high cv.
    steady = {"id": 1, "stats": [_stat(w, 10.0) for w in range(1, 5)]}
    volatile = {"id": 2, "stats": [_stat(w, p) for w, p in zip(range(1, 5), [2, 18, 4, 16])]}
    model = volatility.build([steady, volatile], through_week=5)
    assert model.coefficient_of_variation(1) < 0.05
    assert model.coefficient_of_variation(2) > 0.5


def test_build_requires_minimum_games():
    thin = {"id": 1, "stats": [_stat(1, 10.0), _stat(2, 12.0)]}  # only 2 games
    model = volatility.build([thin], through_week=5)
    assert model.coefficient_of_variation(1) is None


def test_build_skips_players_with_zero_mean():
    zeros = {"id": 1, "stats": [_stat(w, 0.0) for w in range(1, 5)]}
    model = volatility.build([zeros], through_week=5)
    assert model.coefficient_of_variation(1) is None


def test_tilt_factor_favors_ceiling_when_trailing():
    assert volatility.tilt_factor(-25.0) == 1.0   # big underdog -> max ceiling tilt
    assert volatility.tilt_factor(25.0) == -1.0   # big favorite -> max floor tilt
    assert volatility.tilt_factor(0.0) == 0.0
    assert 0.0 < volatility.tilt_factor(-10.0) < 1.0
    assert -1.0 < volatility.tilt_factor(10.0) < 0.0


def test_tilt_factor_is_clipped_beyond_the_full_margin():
    assert volatility.tilt_factor(-100.0) == 1.0
    assert volatility.tilt_factor(100.0) == -1.0


def test_apply_boosts_volatile_players_when_trailing():
    model = volatility.VolatilityModel({1: 0.9})  # well above the 0.5 baseline
    p = make_player(1)
    volatility.apply([p], model, projected_margin=-25.0)  # big underdog
    assert p.matchup_multiplier > 1.0


def test_apply_discounts_volatile_players_when_favored():
    model = volatility.VolatilityModel({1: 0.9})
    p = make_player(1)
    volatility.apply([p], model, projected_margin=25.0)  # big favorite
    assert p.matchup_multiplier < 1.0


def test_apply_leaves_steady_players_alone():
    model = volatility.VolatilityModel({1: 0.5})  # exactly the baseline
    p = make_player(1)
    volatility.apply([p], model, projected_margin=-25.0)
    assert p.matchup_multiplier == 1.0


def test_apply_is_a_noop_in_a_close_matchup():
    model = volatility.VolatilityModel({1: 0.9})
    p = make_player(1)
    volatility.apply([p], model, projected_margin=0.0)
    assert p.matchup_multiplier == 1.0


def test_apply_skips_excluded_and_unknown_players():
    model = volatility.VolatilityModel({1: 0.9})
    excluded = make_player(1)
    excluded.exclusion_reason = "out"
    unknown = make_player(2)
    volatility.apply([excluded, unknown], model, projected_margin=-25.0)
    assert excluded.matchup_multiplier == 1.0
    assert unknown.matchup_multiplier == 1.0


def test_empty_model_is_a_full_noop():
    model = volatility.VolatilityModel({})
    p = make_player(1)
    volatility.apply([p], model, projected_margin=-25.0)
    assert p.matchup_multiplier == 1.0
