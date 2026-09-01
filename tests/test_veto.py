"""The veto window is the safety mechanism, so it gets its own tests."""

import argparse
from datetime import datetime, timezone

import pytest

from fantasyagent import cli
from fantasyagent.agent import LineupAgent
from fantasyagent.state import Proposal, Store
from tests.test_agent import BEFORE_KICKOFF, FakeClient, make_config


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "state")


@pytest.fixture
def config(tmp_path):
    return make_config(state_dir=tmp_path / "state")


@pytest.fixture
def patched_agent(monkeypatch):
    """Route the CLI through FakeClient and expose what it submitted."""
    client = FakeClient()

    class Patched(LineupAgent):
        def __init__(self, cfg, client=None):
            super().__init__(cfg, client=client or globals()["_shared_client"])

        def decide(self, week=None, now=None):
            return super().decide(week=week, now=now or BEFORE_KICKOFF)

    globals()["_shared_client"] = client
    monkeypatch.setattr(cli, "LineupAgent", Patched)
    return client


def args(**kwargs):
    defaults = dict(week=None, dry_run=False, force=False, config="config.yaml", verbose=False)
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def test_propose_saves_a_pending_proposal(config, patched_agent):
    assert cli.cmd_propose(config, args()) == 0
    pending = Store(config.state_dir).load_pending()
    assert pending is not None
    assert pending.moves and not pending.vetoed and not pending.applied
    assert "Starters:" in pending.summary
    assert patched_agent.submitted == [], "propose must never write to ESPN"


def test_veto_blocks_the_submission(config, patched_agent):
    cli.cmd_propose(config, args())
    assert cli.cmd_veto(config, args()) == 0
    assert cli.cmd_apply(config, args()) == 0
    assert patched_agent.submitted == []


def test_apply_submits_when_not_vetoed(config, patched_agent):
    cli.cmd_propose(config, args())
    assert cli.cmd_apply(config, args()) == 0
    assert len(patched_agent.submitted) == 1
    assert Store(config.state_dir).load_pending() is None, "pending is cleared once resolved"


def test_apply_refuses_without_a_proposal(config, patched_agent):
    assert cli.cmd_apply(config, args()) == 1
    assert patched_agent.submitted == []


def test_apply_force_works_without_a_proposal(config, patched_agent):
    assert cli.cmd_apply(config, args(force=True)) == 0
    assert len(patched_agent.submitted) == 1


def test_apply_respects_the_minimum_gain_threshold(tmp_path, patched_agent):
    config = make_config(state_dir=tmp_path / "state", min_gain=1000.0)
    cli.cmd_propose(config, args())
    assert cli.cmd_apply(config, args()) == 0
    assert patched_agent.submitted == []


def test_apply_dry_run_writes_nothing(config, patched_agent):
    cli.cmd_propose(config, args())
    assert cli.cmd_apply(config, args(dry_run=True)) == 0
    assert patched_agent.submitted == []


def test_history_records_every_transition(config, patched_agent):
    cli.cmd_propose(config, args())
    cli.cmd_veto(config, args())
    cli.cmd_apply(config, args())
    events = (config.state_dir / "history.jsonl").read_text().strip().splitlines()
    assert len(events) == 2  # proposed, vetoed; the vetoed run never applies


def test_store_round_trips_a_proposal(store):
    proposal = Proposal.new(season=2026, week=3, team_id=7, moves=[{"player_id": 1}])
    store.save_pending(proposal)
    loaded = store.load_pending()
    assert loaded.matches(2026, 3, 7)
    assert loaded.moves == [{"player_id": 1}]
    store.clear_pending()
    assert store.load_pending() is None


def test_corrupt_pending_file_is_reported_clearly(store):
    (store.dir / "pending.json").write_text("{not json")
    with pytest.raises(RuntimeError, match="corrupt"):
        store.load_pending()
