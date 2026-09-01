from fantasyagent import commands
from fantasyagent.espn.constants import SLOT_BENCH, SLOT_QB, SLOT_RB
from fantasyagent.espn.models import Player, Roster


def make_player(pid, name):
    return Player(
        player_id=pid, name=name, position="RB", pro_team_id=1,
        eligible_slots=[SLOT_RB, SLOT_BENCH], current_slot=SLOT_BENCH,
        injury_status="ACTIVE", projected_points=10.0,
    )


def roster():
    return Roster(team_id=1, team_name="T", players=[
        make_player(1, "Bijan Robinson"), make_player(2, "Jacobs"),
    ])


def test_approve_and_veto_are_recognized_case_and_punctuation_insensitively():
    assert commands.parse_reply("Approve!").approve
    assert commands.parse_reply("  VETO  ").veto
    assert commands.parse_reply("no.").veto
    assert commands.parse_reply("looks good").approve


def test_start_and_bench_lines_are_parsed():
    decision = commands.parse_reply("start Bijan Robinson\nbench Jacobs")
    assert [ (s.action, s.query) for s in decision.swaps ] == [
        ("start", "Bijan Robinson"), ("bench", "Jacobs"),
    ]
    assert decision.understood


def test_unrecognized_text_is_reported_not_guessed():
    decision = commands.parse_reply("what do you think about my WR2 this week?")
    assert not decision.understood
    assert not decision.veto and not decision.approve and not decision.swaps
    assert decision.unrecognized


def test_quoted_reply_history_is_stripped():
    body = "approve\n\nOn Sat, Sep 6, 2026, Fantasy Agent wrote:\n> Week 3 lineup"
    decision = commands.parse_reply(body)
    assert decision.approve
    assert not decision.unrecognized


def test_gt_quoted_lines_are_stripped():
    body = "start Bijan Robinson\n> previous message\n> more quoted text"
    decision = commands.parse_reply(body)
    assert len(decision.swaps) == 1


def test_resolve_swaps_matches_exact_then_substring():
    r = roster()
    swaps = [commands.SwapCommand("start", "bijan robinson"), commands.SwapCommand("bench", "Jacobs")]
    problems = commands.resolve_swaps(swaps, r)
    assert not problems
    assert swaps[0].player.player_id == 1
    assert swaps[1].player.player_id == 2


def test_resolve_swaps_reports_no_match():
    swaps = [commands.SwapCommand("start", "Nobody Here")]
    problems = commands.resolve_swaps(swaps, roster())
    assert "Nobody Here" in problems[0]
    assert swaps[0].player is None


def test_resolve_swaps_reports_ambiguous_match():
    r = Roster(team_id=1, team_name="T", players=[make_player(1, "Josh Allen"), make_player(2, "Josh Jacobs")])
    swaps = [commands.SwapCommand("start", "Josh")]
    problems = commands.resolve_swaps(swaps, r)
    assert "multiple players" in problems[0]
    assert swaps[0].player is None


def test_apply_swaps_stamps_forced_flags():
    r = roster()
    swaps = [commands.SwapCommand("start", "Bijan Robinson"), commands.SwapCommand("bench", "Jacobs")]
    commands.resolve_swaps(swaps, r)
    commands.apply_swaps(swaps)
    assert r.by_id(1).forced_start and not r.by_id(1).forced_bench
    assert r.by_id(2).forced_bench and not r.by_id(2).forced_start


def test_apply_swaps_skips_unresolved():
    swaps = [commands.SwapCommand("start", "Nobody")]
    commands.apply_swaps(swaps)  # player is None; must not raise
