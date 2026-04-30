"""Per-account-id state path resolver.

Each MT5 account gets its own namespace under ~/.trading-agent-skills/accounts/<id>/
so account changes are a clean-slate operation by design.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


_VALID_ACCOUNT_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def _default_base() -> Path:
    return Path.home() / ".trading-agent-skills"


@dataclass(frozen=True)
class AccountPaths:
    root: Path
    charter: Path
    charter_versions: Path
    journal: Path
    decisions: Path
    proposals: Path
    daily_state: Path

    def ensure_dirs(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.charter_versions.mkdir(exist_ok=True)
        self.proposals.mkdir(exist_ok=True)


def resolve_account_paths(*, account_id: str, base: Optional[Path] = None) -> AccountPaths:
    if not account_id or not _VALID_ACCOUNT_ID.match(account_id):
        raise ValueError(
            f"account_id must be non-empty alphanumeric (with - or _), got {account_id!r}"
        )
    root = (base or _default_base()) / "accounts" / account_id
    return AccountPaths(
        root=root,
        charter=root / "charter.md",
        charter_versions=root / "charter_versions",
        journal=root / "journal.jsonl",
        decisions=root / "decisions.jsonl",
        proposals=root / "proposals",
        daily_state=root / "daily_state.json",
    )
