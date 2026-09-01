"""Command-line entry point.

Weekly rhythm:
    propose   (Saturday)        work out the lineup, save it, tell you about it
    apply     (Sunday morning)  re-check late news, then submit unless vetoed
    veto                        cancel this week's automatic submission
    status                      show what is pending
    run                         propose and apply in one shot, for manual use
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from typing import Optional

from . import notify
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
    store.save_pending(proposal)
    store.record(proposal, "proposed")

    if decision.lineup.has_changes:
        text += "\n\nThis will be submitted automatically. Run `fantasyagent veto` to stop it."
    _announce(config, text)
    return 0


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

    # Re-decide from scratch rather than replaying Saturday's plan: Sunday
    # morning inactive reports are exactly the news worth waiting for.
    decision = _decide(config, args.week)
    text = notify.render(
        decision.roster, decision.lineup, decision.week, current_total=decision.current_total
    )

    if not decision.lineup.has_changes:
        _announce(config, text)
        _finish(store, pending, applied=False)
        return 0

    if decision.gain < config.min_gain and not args.force:
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
    sub.add_parser("propose", help="Compute and save this week's lineup.")

    apply_p = sub.add_parser("apply", help="Submit the lineup unless vetoed.")
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
