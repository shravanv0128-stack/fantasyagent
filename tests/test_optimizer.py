from fantasyagent.espn.constants import (
    SLOT_BENCH, SLOT_DST, SLOT_FLEX, SLOT_K, SLOT_QB, SLOT_RB, SLOT_TE, SLOT_WR,
)
from fantasyagent.espn.models import Player, Roster
from fantasyagent.optimizer import bench, optimize, starters

STANDARD = {SLOT_QB: 1, SLOT_RB: 2, SLOT_WR: 2, SLOT_TE: 1, SLOT_FLEX: 1, SLOT_DST: 1, SLOT_K: 1}

ELIGIBLE = {
    "QB": [SLOT_QB, SLOT_BENCH],
    "RB": [SLOT_RB, SLOT_FLEX, SLOT_BENCH],
    "WR": [SLOT_WR, SLOT_FLEX, SLOT_BENCH],
    "TE": [SLOT_TE, SLOT_FLEX, SLOT_BENCH],
    "K": [SLOT_K, SLOT_BENCH],
    "D/ST": [SLOT_DST, SLOT_BENCH],
}


def make_player(pid, name, position, points, slot=SLOT_BENCH, **kwargs):
    return Player(
        player_id=pid,
        name=name,
        position=position,
        pro_team_id=kwargs.pop("pro_team_id", 1),
        eligible_slots=ELIGIBLE[position],
        current_slot=slot,
        injury_status=kwargs.pop("injury_status", "ACTIVE"),
        projected_points=points,
        **kwargs,
    )


def full_roster(**overrides):
    spec = [
        (1, "QB1", "QB", 20.0), (2, "QB2", "QB", 14.0),
        (3, "RB1", "RB", 18.0), (4, "RB2", "RB", 12.0), (5, "RB3", "RB", 9.0),
        (6, "WR1", "WR", 17.0), (7, "WR2", "WR", 13.0), (8, "WR3", "WR", 11.0),
        (9, "TE1", "TE", 10.0), (10, "TE2", "TE", 4.0),
        (11, "K1", "K", 8.0), (12, "DST1", "D/ST", 7.0),
    ]
    return Roster(team_id=1, team_name="Test", players=[
        make_player(pid, name, pos, overrides.get(name, pts))
        for pid, name, pos, pts in spec
    ])


def test_picks_highest_scoring_legal_lineup():
    lineup = optimize(full_roster(), STANDARD)
    started = {p.name for p in starters(full_roster(), lineup)}
    assert started == {"QB1", "RB1", "RB2", "WR1", "WR2", "WR3", "TE1", "K1", "DST1"}
    # WR3 (11.0) beats RB3 (9.0) for the flex.
    assert lineup.assignments[8] == SLOT_FLEX
    assert lineup.projected_total == 20 + 18 + 12 + 17 + 13 + 11 + 10 + 8 + 7


def test_assignment_beats_greedy_slot_filling():
    """Greedy would burn the flex on the best remaining player and strand a slot."""
    roster = Roster(team_id=1, team_name="T", players=[
        make_player(1, "QB1", "QB", 20.0),
        make_player(2, "RB1", "RB", 30.0),
        make_player(3, "RB2", "RB", 5.0),
        make_player(4, "WR1", "WR", 25.0),
        make_player(5, "WR2", "WR", 24.0),
        make_player(6, "WR3", "WR", 23.0),
        make_player(7, "TE1", "TE", 22.0),
        make_player(8, "K1", "K", 8.0),
        make_player(9, "DST1", "D/ST", 7.0),
    ])
    lineup = optimize(roster, STANDARD)
    # Only two RBs exist, so both must start; the flex has to take a WR.
    assert lineup.assignments[3] == SLOT_RB
    assert lineup.assignments[6] == SLOT_FLEX
    assert not bench(roster, lineup)


def test_excluded_player_is_benched_for_a_worse_but_available_one():
    roster = full_roster()
    roster.by_id(3).exclusion_reason = "out"  # RB1, the best RB
    lineup = optimize(roster, STANDARD)
    assert lineup.assignments[3] == SLOT_BENCH
    assert lineup.assignments[5] == SLOT_RB  # RB3 promoted


def test_no_moves_when_lineup_is_already_optimal():
    roster = full_roster()
    optimal = optimize(full_roster(), STANDARD)
    for player in roster.players:
        player.current_slot = optimal.assignments[player.player_id]
    assert not optimize(roster, STANDARD).has_changes


def test_incumbent_keeps_slot_on_an_exact_tie():
    roster = Roster(team_id=1, team_name="T", players=[
        make_player(1, "QB1", "QB", 10.0, slot=SLOT_QB),
        make_player(2, "QB2", "QB", 10.0, slot=SLOT_BENCH),
    ])
    lineup = optimize(roster, {SLOT_QB: 1})
    assert lineup.assignments[1] == SLOT_QB
    assert not lineup.has_changes


def test_locked_player_is_never_moved():
    roster = full_roster(WR3=5.0)
    weak_starter = roster.by_id(10)  # TE2, 4.0 points
    weak_starter.current_slot = SLOT_TE
    weak_starter.game_started = True
    roster.by_id(9).current_slot = SLOT_BENCH  # TE1 sits, 10.0 points

    lineup = optimize(roster, STANDARD)
    assert lineup.assignments[10] == SLOT_TE
    assert 10 not in {m.player.player_id for m in lineup.moves}
    # TE1 is still eligible for the flex, so the points are not simply lost.
    assert lineup.assignments[9] == SLOT_FLEX


def test_short_roster_leaves_slot_unfilled_rather_than_crashing():
    roster = Roster(team_id=1, team_name="T", players=[make_player(1, "QB1", "QB", 20.0)])
    lineup = optimize(roster, STANDARD)
    assert lineup.assignments[1] == SLOT_QB
    assert sorted(lineup.unfilled_slots) == sorted([SLOT_RB, SLOT_RB, SLOT_WR, SLOT_WR,
                                                    SLOT_TE, SLOT_FLEX, SLOT_DST, SLOT_K])


def test_empty_roster_is_handled():
    lineup = optimize(Roster(team_id=1, team_name="T", players=[]), STANDARD)
    assert not lineup.has_changes
    assert len(lineup.unfilled_slots) == 9
