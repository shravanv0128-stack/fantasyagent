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


def email_instructions() -> str:
    """Keyword reference appended to the plain-text half of the proposal email."""
    return (
        "Reply to this email with one of these words on its own line:\n"
        "  approve  - lock this lineup in right now, don't wait for Sunday\n"
        "  veto     - leave your current ESPN lineup untouched this week\n"
        "You can also add lines like:\n"
        "  start <player name>  - force that player into the lineup\n"
        "  bench <player name>  - force that player out of the lineup\n"
        "\n"
        "No reply needed either way: if you don't respond, this lineup is "
        "submitted automatically Sunday morning."
    )


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


def _start_reason(player) -> str:
    bits = [f"{player.projected_points:.1f} pts projected"]
    if abs(player.matchup_multiplier - 1.0) >= 0.02:
        bits.append("tough matchup" if player.matchup_multiplier < 1 else "soft matchup")
    if player.injury_status == "QUESTIONABLE":
        bits.append("questionable, discounted")
    if player.note:
        bits.append(player.note)
    return "; ".join(bits)


def _bench_reason(player) -> str:
    if player.exclusion_reason:
        return player.exclusion_reason.capitalize()
    if player.note:
        return player.note
    return "Lower projection than the starter(s) at this position"


def _escape(text: str) -> str:
    return (
        str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def render_html(roster: Roster, lineup: Lineup, week: int, *, current_total: float) -> str:
    """A concise HTML table for the weekly proposal email."""
    rows = []
    for player in starters(roster, lineup):
        slot = SLOT_NAMES.get(lineup.assignments[player.player_id], "?")
        rows.append(
            "<tr>"
            f"<td>{_escape(slot)}</td><td>{_escape(player.name)}</td>"
            f"<td>{_escape(player.pro_team)}</td><td>{player.score:.1f}</td>"
            f"<td>{_escape(_start_reason(player))}</td>"
            "</tr>"
        )

    bench_rows = []
    for player in bench(roster, lineup):
        bench_rows.append(
            "<tr>"
            f"<td>{_escape(player.name)}</td><td>{_escape(player.pro_team)}</td>"
            f"<td>{player.score:.1f}</td><td>{_escape(_bench_reason(player))}</td>"
            "</tr>"
        )

    gain = lineup.projected_total - current_total
    change_note = (
        f"Proposed changes: +{gain:.1f} projected points over your current lineup."
        if lineup.has_changes
        else "No changes — your current lineup is already optimal."
    )

    style = (
        "font-family:-apple-system,Helvetica,Arial,sans-serif;color:#1a1a1a;"
        "max-width:640px;margin:0 auto;"
    )
    table_style = "border-collapse:collapse;width:100%;font-size:14px;margin:12px 0;"
    th_style = "text-align:left;padding:6px 8px;border-bottom:2px solid #ddd;color:#555;"
    td_style = "padding:6px 8px;border-bottom:1px solid #eee;"

    return f"""\
<div style="{style}">
  <h2 style="margin-bottom:4px;">Week {week} lineup — {_escape(roster.team_name)}</h2>
  <p style="color:#444;">{_escape(change_note)}</p>

  <h3 style="margin-bottom:4px;">Starting</h3>
  <table style="{table_style}">
    <tr>
      <th style="{th_style}">Slot</th><th style="{th_style}">Player</th>
      <th style="{th_style}">Team</th><th style="{th_style}">Proj</th>
      <th style="{th_style}">Why</th>
    </tr>
    {''.join(row.replace("<td>", f'<td style="{td_style}">') for row in rows)}
  </table>

  <h3 style="margin-bottom:4px;">Bench</h3>
  <table style="{table_style}">
    <tr>
      <th style="{th_style}">Player</th><th style="{th_style}">Team</th>
      <th style="{th_style}">Proj</th><th style="{th_style}">Why not</th>
    </tr>
    {''.join(row.replace("<td>", f'<td style="{td_style}">') for row in bench_rows)}
  </table>

  <p style="margin-top:20px;padding:12px;background:#f5f5f7;border-radius:8px;font-size:14px;">
    <b>Reply to this email</b> with <code>approve</code> to lock it in now,
    <code>veto</code> to leave your current lineup untouched, or lines like
    <code>start Player Name</code> / <code>bench Player Name</code> to adjust it.<br>
    If you don't reply, this lineup is submitted automatically Sunday morning.
  </p>
</div>
"""
