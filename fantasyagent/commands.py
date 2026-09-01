"""Parse a plain-English email reply into a decision.

Deliberately keyword-only, not free-text AI parsing: a misread swap command
that silently benches the wrong player is worse than one that is ignored. Every
line is matched against a small fixed vocabulary; anything else is reported
back to the user as unrecognized rather than guessed at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from .espn.models import Player, Roster

_VETO_WORDS = {"veto", "no", "stop", "cancel", "skip"}
_APPROVE_WORDS = {"approve", "yes", "ok", "okay", "confirm", "confirmed", "looks good", "lgtm"}

_START_RE = re.compile(r"^\s*start\s+(.+?)\s*$", re.IGNORECASE)
_BENCH_RE = re.compile(r"^\s*(?:bench|sit)\s+(.+?)\s*$", re.IGNORECASE)


@dataclass
class SwapCommand:
    action: str  # "start" or "bench"
    query: str
    player: Optional[Player] = None


@dataclass
class ReplyDecision:
    #: True if any recognized instruction was found at all.
    understood: bool = False
    veto: bool = False
    approve: bool = False
    swaps: List[SwapCommand] = field(default_factory=list)
    unrecognized: List[str] = field(default_factory=list)


def _strip_quoted_reply(body: str) -> str:
    """Drop the quoted original message most mail clients append to a reply."""
    lines = body.replace("\r\n", "\n").split("\n")
    kept = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(">"):
            break
        if re.match(r"^on .+ wrote:$", stripped, re.IGNORECASE):
            break
        if re.match(r"^-{2,}\s*original message\s*-{2,}$", stripped, re.IGNORECASE):
            break
        kept.append(line)
    return "\n".join(kept).strip()


def parse_reply(body: str) -> ReplyDecision:
    decision = ReplyDecision()
    text = _strip_quoted_reply(body)

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        lowered = line.lower().strip(" .!")

        if lowered in _VETO_WORDS:
            decision.veto = True
            decision.understood = True
            continue
        if lowered in _APPROVE_WORDS:
            decision.approve = True
            decision.understood = True
            continue

        match = _START_RE.match(line)
        if match:
            decision.swaps.append(SwapCommand("start", match.group(1)))
            decision.understood = True
            continue

        match = _BENCH_RE.match(line)
        if match:
            decision.swaps.append(SwapCommand("bench", match.group(1)))
            decision.understood = True
            continue

        decision.unrecognized.append(line)

    return decision


def resolve_swaps(swaps: List[SwapCommand], roster: Roster) -> List[str]:
    """Match each swap's free-text query to a roster player, in place.

    Returns a list of human-readable problems (no match, or more than one).
    """
    problems: List[str] = []
    for swap in swaps:
        needle = swap.query.strip().lower()
        exact = [p for p in roster.players if p.name.lower() == needle]
        candidates = exact or [p for p in roster.players if needle in p.name.lower()]

        if not candidates:
            problems.append(f"Could not find '{swap.query}' on your roster — ignored.")
        elif len(candidates) > 1:
            names = ", ".join(p.name for p in candidates)
            problems.append(f"'{swap.query}' matches multiple players ({names}) — ignored.")
        else:
            swap.player = candidates[0]
    return problems


def apply_swaps(swaps: List[SwapCommand]) -> None:
    """Stamp resolved swap commands onto their players' forced flags."""
    for swap in swaps:
        if swap.player is None:
            continue
        if swap.action == "start":
            swap.player.forced_start = True
            swap.player.forced_bench = False
            swap.player.note = "start requested by reply"
        else:
            swap.player.forced_bench = True
            swap.player.forced_start = False
            swap.player.note = "bench requested by reply"
