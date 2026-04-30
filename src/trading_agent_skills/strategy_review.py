"""Weekly strategy review — aggregates journal + decision-log + charter, emits
a markdown proposal that the user approves before any charter change is written.

This module ONLY produces proposals. It NEVER mutates the charter — that is the
caller's job after explicit user approval.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from trading_agent_skills.account_paths import AccountPaths
from trading_agent_skills.journal_io import read_resolved


def compute_performance_summary(
    paths: AccountPaths,
    *,
    since: datetime,
    until: datetime,
) -> dict[str, Any]:
    """Aggregate journal entries within [since, until) into a summary dict."""
    if not paths.journal.is_file():
        return _empty_summary()

    closed = [
        e for e in read_resolved(paths.journal)
        if _within(e.get("entry_time"), since, until) or _within(e.get("exit_time"), since, until)
    ]
    if not closed:
        return _empty_summary()

    wins = sum(1 for e in closed if Decimal(e["realized_pnl"]) > 0)
    losses = sum(1 for e in closed if Decimal(e["realized_pnl"]) < 0)
    pnl = sum((Decimal(e["realized_pnl"]) for e in closed), Decimal("0"))

    return {
        "trades_closed": len(closed),
        "wins": wins,
        "losses": losses,
        "win_rate": float(wins) * 100.0 / len(closed) if closed else None,
        "realized_pnl": format(pnl, "f"),
    }


def _empty_summary() -> dict[str, Any]:
    return {
        "trades_closed": 0, "wins": 0, "losses": 0,
        "win_rate": None, "realized_pnl": "0",
    }


def _within(ts: Optional[str], since: datetime, until: datetime) -> bool:
    if not ts:
        return False
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return since <= dt < until
