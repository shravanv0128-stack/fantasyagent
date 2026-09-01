"""End-to-end coverage of the email propose/reply/apply loop."""

import argparse

import pytest

from fantasyagent import cli, mail
from fantasyagent.agent import LineupAgent
from fantasyagent.state import Store
from tests.test_agent import BEFORE_KICKOFF, FakeClient, make_config


class FakeMailbox:
    """Stands in for the Gmail send/fetch functions the CLI calls."""

    def __init__(self):
        self.sent = []
        self.inbox = []  # list of mail.Reply

    def send(self, creds, to, subject, text_body, html_body):
        message_id = f"<msg-{len(self.sent)}@test>"
        self.sent.append({"to": to, "subject": subject, "text": text_body, "html": html_body, "id": message_id})
        assert "<html" not in text_body  # the plain-text part must stay plain
        assert "<table" in html_body
        return message_id

    def fetch_replies(self, creds, in_reply_to, timeout=30):
        return [r for _, r in self.inbox if _ == in_reply_to]

    def reply(self, message_id, body, sender="you@example.com"):
        self.inbox.append((message_id, mail.Reply(sender=sender, body=body, received_at=0.0)))


@pytest.fixture
def config(tmp_path):
    return make_config(
        state_dir=tmp_path / "state",
        email_to="sv1066@princeton.edu",
        gmail_address="agent@example.com",
        gmail_app_password="app-password",
    )


@pytest.fixture
def patched(monkeypatch):
    box = FakeMailbox()
    client = FakeClient()

    monkeypatch.setattr(mail, "send", box.send)
    monkeypatch.setattr(mail, "fetch_replies", box.fetch_replies)

    class Patched(LineupAgent):
        def __init__(self, cfg, client=None):
            super().__init__(cfg, client=client or client_holder["client"])

        def decide(self, week=None, now=None, forced_start=None, forced_bench=None):
            return super().decide(
                week=week, now=now or BEFORE_KICKOFF,
                forced_start=forced_start, forced_bench=forced_bench,
            )

    client_holder = {"client": client}
    monkeypatch.setattr(cli, "LineupAgent", Patched)
    return box, client


def args(**kwargs):
    defaults = dict(week=None, dry_run=False, force=False, config="config.yaml", verbose=False)
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def test_propose_sends_an_email_and_stores_the_message_id(config, patched):
    box, _ = patched
    assert cli.cmd_propose(config, args()) == 0
    assert len(box.sent) == 1
    assert box.sent[0]["to"] == "sv1066@princeton.edu"
    pending = Store(config.state_dir).load_pending()
    assert pending.email_message_id == box.sent[0]["id"]


def test_approve_reply_submits_even_below_min_gain(tmp_path, patched):
    box, client = patched
    config = make_config(
        state_dir=tmp_path / "state", email_to="a@b.com",
        gmail_address="x@y.com", gmail_app_password="pw", min_gain=1000.0,
    )
    cli.cmd_propose(config, args())
    pending = Store(config.state_dir).load_pending()
    box.reply(pending.email_message_id, "approve")

    assert cli.cmd_apply(config, args()) == 0
    assert len(client.submitted) == 1


def test_veto_reply_blocks_submission(config, patched):
    box, client = patched
    cli.cmd_propose(config, args())
    pending = Store(config.state_dir).load_pending()
    box.reply(pending.email_message_id, "Veto")

    assert cli.cmd_apply(config, args()) == 0
    assert client.submitted == []
    assert Store(config.state_dir).load_pending().vetoed


def test_swap_reply_is_applied_and_submitted(config, patched):
    box, client = patched
    cli.cmd_propose(config, args())
    pending = Store(config.state_dir).load_pending()
    box.reply(pending.email_message_id, "start Bench RB\nbench Solid RB")

    assert cli.cmd_apply(config, args()) == 0
    team_id, week, moves = client.submitted[0]
    by_player = {m["player_id"]: m["to_slot"] for m in moves}
    from fantasyagent.espn.constants import SLOT_BENCH, SLOT_RB
    assert by_player[5] == SLOT_RB      # Bench RB promoted per the reply
    assert by_player[3] == SLOT_BENCH   # Solid RB benched per the reply


def test_unresolvable_swap_name_is_noted_but_does_not_crash(config, patched):
    box, client = patched
    cli.cmd_propose(config, args())
    pending = Store(config.state_dir).load_pending()
    box.reply(pending.email_message_id, "start Nobody On My Roster")

    assert cli.cmd_apply(config, args()) == 0  # still runs the normal decision


def test_no_reply_still_auto_applies(config, patched):
    box, client = patched
    cli.cmd_propose(config, args())
    assert cli.cmd_apply(config, args()) == 0
    assert len(client.submitted) == 1


def test_email_disabled_skips_reply_check_entirely(tmp_path, patched):
    box, client = patched
    config = make_config(state_dir=tmp_path / "state")  # no email fields set
    cli.cmd_propose(config, args())
    assert box.sent == []
    assert cli.cmd_apply(config, args()) == 0
    assert len(client.submitted) == 1
