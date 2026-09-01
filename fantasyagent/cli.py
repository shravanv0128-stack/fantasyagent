"""Command-line entry point.

Weekly rhythm:
    propose   (Saturday)        work out the lineup, save it, email it to you
    apply     (Sunday morning)  read your reply, re-check late news, submit
    veto                        cancel this week's automatic submission
    status                      show what is pending
    run                         propose and apply in one shot, for manual use
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from typing import List, Optional

from . import commands, mail, notify
from .agent import Decision, LineupAgent
from .config import Config, ConfigError, load
from .espn.client import ESPNError
from .state import Proposal, Store

log = logging.getLogger("fantasyagent")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )


def _announce(config: Config, text: str) -> None:
    print(text)
    notify.to_slack(config.slack_webhook, text)


def _mail_creds(config: Config) -> mail.Credentials:
    return mail.Credentials(address=config.gmail_address, app_password=config.gmail_app_password)


def _decide(config: Config, week: Optional[int]) -> Decision:
    return LineupAgent(config).decide(week=week)


def cmd_propose(config: Config, args: argparse.Namespace) -> int:
    decision = _decide(config, args.week)
    text = notify.render(
        decision.roster, decision.lineup, decision.week, current_total=decision.current_total
    )

    store = Store(config.state_dir)
    proposal = Proposal.new(
        season=config.season,
        week=decision.week,
        team_id=decision.roster.team_id,
        moves=[m.as_payload() for m in decision.lineup.moves],
        summary=text,
        projected_total=round(decision.lineup.projected_total, 2),
        projected_gain=round(decision.gain, 2),
    )

    if config.email_enabled:
        html = notify.render_html(
            decision.roster, decision.lineup, decision.week, current_total=decision.current_total
        )
        email_text = text + "\n\n" + notify.email_instructions()
        try:
            message_id = mail.send(
                _mail_creds(config),
                to=config.email_to,
                subject=f"Week {decision.week} fantasy lineup — {decision.roster.team_name}",
                text_body=email_text,
                html_body=html,
            )
            proposal.email_message_id = message_id
            log.info("Proposal emailed to %s", config.email_to)
        except mail.MailError as exc:
            log.warning("Could not send proposal email: %s", exc)

    store.save_pending(proposal)
    store.record(proposal, "proposed")

    if decision.lineup.has_changes:
        text += "\n\nThis will be submitted automatically. Run `fantasyagent veto` to stop it."
    _announce(config, text)
    return 0


def _read_email_decision(config: Config, pending: Optional[Proposal]) -> Optional[commands.ReplyDecision]:
    """The most recent reply in the proposal thread, parsed, or None if
    there is nothing to read (email disabled, no proposal, or no reply yet)."""
    if not config.email_enabled or pending is None or not pending.email_message_id:
        return None
    try:
        replies = mail.fetch_replies(_mail_creds(config), pending.email_message_id)
    except mail.MailError as exc:
        log.warning("Could not check for a reply: %s", exc)
        return None
    if not replies:
        return None
    # Only the latest reply counts, so a change of mind supersedes an earlier one.
    latest = replies[-1]
    log.info("Read reply from %s", latest.sender)
    return commands.parse_reply(latest.body)


def _apply_swaps(decision: Decision, reply: commands.ReplyDecision) -> List[str]:
    """Resolve and stamp swap commands onto ``decision``'s roster in place.

    Returns problems to surface to the user (unmatched or ambiguous names).
    """
    problems = commands.resolve_swaps(reply.swaps, decision.roster)
    commands.apply_swaps(reply.swaps)
    return problems


def cmd_apply(config: Config, args: argparse.Namespace) -> int:
    store = Store(config.state_dir)
    pending = store.load_pending()

    if pending and pending.vetoed:
        _announce(config, f"Week {pending.week}: vetoed — leaving your lineup alone.")
        return 0
    if pending and pending.applied:
        log.info("Week %s proposal was already applied.", pending.week)
        return 0
    if pending is None and not args.force:
        log.error(
            "No pending proposal. Run `propose` first, or pass --force to decide "
            "and submit in one step."
        )
        return 1

    reply = _read_email_decision(config, pending)
    if reply and reply.veto:
        if pending is not None:
            pending.vetoed = True
            pending.vetoed_at = datetime.now(timezone.utc).isoformat()
            store.save_pending(pending)
            store.record(pending, "vetoed-by-email")
        _announce(config, f"Vetoed by email reply — leaving your lineup alone.")
        return 0

    # Re-decide from scratch rather than replaying Saturday's plan: Sunday
    # morning inactive reports are exactly the news worth waiting for.
    decision = _decide(config, args.week)

    manual_override = False
    notes: List[str] = []
    if reply and reply.swaps:
        problems = _apply_swaps(decision, reply)
        notes.extend(problems)
        decision = LineupAgent(config).reoptimize(decision)
        manual_override = True
    if reply and reply.approve:
        manual_override = True
    if reply and reply.unrecognized:
        notes.append(
            "Didn't understand: " + "; ".join(f"'{l}'" for l in reply.unrecognized)
        )

    text = notify.render(
        decision.roster, decision.lineup, decision.week, current_total=decision.current_total
    )
    if notes:
        text += "\n\n" + "\n".join(notes)

    if not decision.lineup.has_changes:
        _announce(config, text)
        _finish(store, pending, applied=False)
        return 0

    if decision.gain < config.min_gain and not args.force and not manual_override:
        _announce(
            config,
            text + f"\n\nSkipped: only +{decision.gain:.1f} projected, "
            f"below the {config.min_gain} point threshold.",
        )
        _finish(store, pending, applied=False)
        return 0

    try:
        submitted = LineupAgent(config).submit(decision, dry_run=args.dry_run)
    except ESPNError as exc:
        _announce(config, text + f"\n\nSUBMIT FAILED: {exc}\nSet your lineup manually.")
        return 1

    suffix = "\n\nDry run — nothing submitted." if args.dry_run else "\n\nSubmitted to ESPN."
    _announce(config, text + suffix)
    _finish(store, pending, applied=submitted)
    return 0


def _finish(store: Store, pending: Optional[Proposal], applied: bool) -> None:
    if pending is None:
        return
    pending.applied = applied
    pending.applied_at = datetime.now(timezone.utc).isoformat()
    store.record(pending, "applied" if applied else "no-op")
    store.clear_pending()


def cmd_veto(config: Config, args: argparse.Namespace) -> int:
    store = Store(config.state_dir)
    pending = store.load_pending()
    if pending is None:
        print("Nothing pending to veto.")
        return 0
    pending.vetoed = True
    pending.vetoed_at = datetime.now(timezone.utc).isoformat()
    store.save_pending(pending)
    store.record(pending, "vetoed")
    print(f"Week {pending.week} vetoed. Your lineup will not be touched.")
    return 0


def cmd_status(config: Config, args: argparse.Namespace) -> int:
    pending = Store(config.state_dir).load_pending()
    if pending is None:
        print("No pending proposal.")
        return 0
    state = "vetoed" if pending.vetoed else "applied" if pending.applied else "awaiting submission"
    print(f"Week {pending.week} — {state} (proposed {pending.created_at})\n")
    print(pending.summary)
    return 0


def cmd_run(config: Config, args: argparse.Namespace) -> int:
    result = cmd_propose(config, args)
    if result != 0:
        return result
    args.force = True
    return cmd_apply(config, args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fantasyagent",
        description="Set an ESPN fantasy football lineup, with a veto window.",
    )
    parser.add_argument("-c", "--config", default="config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--week", type=int, help="Override the scoring period.")

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("propose", help="Compute, save, and email this week's lineup.")

    apply_p = sub.add_parser("apply", help="Read any reply, then submit unless vetoed.")
    apply_p.add_argument("--dry-run", action="store_true", help="Decide but do not write.")
    apply_p.add_argument("--force", action="store_true", help="Submit without a pending proposal.")

    sub.add_parser("veto", help="Cancel this week's automatic submission.")
    sub.add_parser("status", help="Show the pending proposal.")

    run_p = sub.add_parser("run", help="Propose and submit immediately.")
    run_p.add_argument("--dry-run", action="store_true")
    return parser


COMMANDS = {
    "propose": cmd_propose,
    "apply": cmd_apply,
    "veto": cmd_veto,
    "status": cmd_status,
    "run": cmd_run,
}


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    for flag in ("dry_run", "force"):
        setattr(args, flag, getattr(args, flag, False))
    try:
        config = load(args.config)
    except ConfigError as exc:
        log.error("%s", exc)
        return 2
    try:
        return COMMANDS[args.command](config, args)
    except ESPNError as exc:
        log.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
