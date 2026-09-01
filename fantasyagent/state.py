"""Durable state for the veto window.

``propose`` writes a pending proposal; ``apply`` reads it back, checks it has
not been vetoed or gone stale, and submits. Everything is plain JSON on disk so
it is easy to inspect, diff, and commit from CI.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PENDING_FILE = "pending.json"
LOG_FILE = "history.jsonl"


@dataclass
class Proposal:
    season: int
    week: int
    team_id: int
    created_at: str
    moves: List[Dict[str, int]] = field(default_factory=list)
    #: Human-readable rendering, kept so the audit log explains itself later.
    summary: str = ""
    projected_total: float = 0.0
    projected_gain: float = 0.0
    vetoed: bool = False
    vetoed_at: Optional[str] = None
    applied: bool = False
    applied_at: Optional[str] = None
    #: Message-ID of the proposal email, used to find replies in the same thread.
    email_message_id: Optional[str] = None

    @classmethod
    def new(cls, season: int, week: int, team_id: int, **kwargs: Any) -> "Proposal":
        return cls(
            season=season,
            week=week,
            team_id=team_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            **kwargs,
        )

    def matches(self, season: int, week: int, team_id: int) -> bool:
        return (self.season, self.week, self.team_id) == (season, week, team_id)


class Store:
    def __init__(self, directory: Path) -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    @property
    def _pending_path(self) -> Path:
        return self.dir / PENDING_FILE

    def load_pending(self) -> Optional[Proposal]:
        path = self._pending_path
        if not path.exists():
            return None
        try:
            return Proposal(**json.loads(path.read_text()))
        except (ValueError, TypeError) as exc:
            raise RuntimeError(f"{path} is corrupt ({exc}). Delete it and re-run propose.") from exc

    def save_pending(self, proposal: Proposal) -> None:
        self._pending_path.write_text(json.dumps(asdict(proposal), indent=2) + "\n")

    def clear_pending(self) -> None:
        self._pending_path.unlink(missing_ok=True)

    def record(self, proposal: Proposal, event: str) -> None:
        entry = {"event": event, "at": datetime.now(timezone.utc).isoformat(), **asdict(proposal)}
        with (self.dir / LOG_FILE).open("a") as fh:
            fh.write(json.dumps(entry) + "\n")
