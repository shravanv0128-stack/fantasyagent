"""Configuration loading: YAML file plus environment for secrets."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


class ConfigError(RuntimeError):
    pass


@dataclass
class Config:
    league_id: int
    season: int
    team_id: Optional[int] = None
    team_name: Optional[str] = None

    swid: Optional[str] = None
    espn_s2: Optional[str] = None

    #: Skip submitting unless the new lineup beats the current one by this much.
    min_gain: float = 0.5
    use_matchup: bool = True
    matchup_alpha: float = 0.06
    #: Never move a player whose game has already kicked off.
    respect_locks: bool = True

    state_dir: Path = Path("state")
    slack_webhook: Optional[str] = None

    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.team_id is None and not self.team_name:
            raise ConfigError("Set either team_id or team_name in config.yaml.")
        self.state_dir = Path(self.state_dir)

    @property
    def has_credentials(self) -> bool:
        return bool(self.swid and self.espn_s2)


def load(path: str = "config.yaml") -> Config:
    file_path = Path(path)
    if not file_path.exists():
        raise ConfigError(
            f"{file_path} not found. Copy config.yaml.example to {file_path} and fill it in."
        )
    data = yaml.safe_load(file_path.read_text()) or {}

    for required in ("league_id", "season"):
        if required not in data:
            raise ConfigError(f"{file_path} is missing required key '{required}'.")

    known = {
        "league_id", "season", "team_id", "team_name", "min_gain",
        "use_matchup", "matchup_alpha", "respect_locks", "state_dir",
    }
    kwargs = {k: v for k, v in data.items() if k in known}
    kwargs["extra"] = {k: v for k, v in data.items() if k not in known}

    kwargs["swid"] = os.environ.get("ESPN_SWID") or None
    kwargs["espn_s2"] = os.environ.get("ESPN_S2") or None
    kwargs["slack_webhook"] = os.environ.get("FANTASY_SLACK_WEBHOOK") or None

    return Config(**kwargs)
