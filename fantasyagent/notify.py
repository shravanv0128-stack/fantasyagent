"""Render a lineup decision and push it wherever the user will see it."""

from __future__ import annotations

import json
import logging
from typing import List, Optional

import requests

from .espn.constants import SLOT_NAMES
from .espn.models import Roster
from .optimizer import Lineup, bench, starters

log = logging.getLogger(__name__)


def render(roster: Roster, lineup: Lineup, week: int, *, current_total: float) -> str:
    lines: List[str] = [f"Week {week} lineup — {roster.team_name}", ""]

    if lineup.moves:
        gain = lineup.projected_total - current_total
        lines.append(f"Proposed changes (+{gain:.1f} projected):")
        for move in lineup.moves:
            reason = _reason(move.player)
            lines.append(f"  {move.describe()}{reason}")
    else:
        lines.append("No changes — the current lineup is already optimal.")
    lines.append("")

    lines.append("Starters:")
    for player in starters(roster, lineup):
        slot = SLOT_NAMES.get(lineup.assignments[player.player_id], "?")
        lines.append(
            f"  {slot:<5} {player.name:<24} {player.pro_team:<4} "
            f"{player.score:5.1f}{_flag(player)}"
        )
    lines.append(f"  {'':<5} {'TOTAL':<24} {'':<4} {lineup.projected_total:5.1f}")

    sat = bench(roster, lineup)
    if sat:
        lines.append("")
        lines.append("Bench:")
        for player in sat[:10]:
            lines.append(
                f"        {player.name:<24} {player.pro_team:<4} "
                f"{player.score:5.1f}{_flag(player)}"
            )

    if lineup.locked:
        lines.append("")
        lines.append(
            "Locked (game already started, cannot be moved): "
            + ", ".join(p.name for p in lineup.locked)
        )
    if lineup.unfilled_slots:
        lines.append("")
        lines.append(
            "No eligible player for: "
            + ", ".join(SLOT_NAMES.get(s, str(s)) for s in lineup.unfilled_slots)
        )
    return "\n".join(lines)


def _flag(player) -> str:
    if player.exclusion_reason:
        return f"  [{player.exclusion_reason}]"
    if player.injury_status not in ("ACTIVE", "NORMAL"):
        return f"  [{player.injury_status.lower()}]"
    return ""


def _reason(player) -> str:
    if player.exclusion_reason:
        return f"  ({player.exclusion_reason})"
    return ""


def to_slack(webhook: Optional[str], text: str) -> bool:
    """Post to a Slack incoming webhook. Never raises — notification failure
    must not take down a lineup run."""
    if not webhook:
        return False
    try:
        resp = requests.post(
            webhook,
            data=json.dumps({"text": f"```{text}```"}),
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        log.warning("Slack notification failed: %s", exc)
        return False
