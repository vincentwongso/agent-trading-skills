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
from trading_agent_skills.decision_log import filter_decisions
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


def compute_setup_breakdown(
    paths: AccountPaths,
    *,
    since: datetime,
    until: datetime,
) -> list[dict[str, Any]]:
    """Group closed trades by setup_type, return list of {setup_type, wins, losses, pnl}."""
    if not paths.journal.is_file():
        return []
    closed = [
        e for e in read_resolved(paths.journal)
        if _within(e.get("entry_time"), since, until) or _within(e.get("exit_time"), since, until)
    ]
    by_setup: dict[str, dict[str, Any]] = {}
    for e in closed:
        st = e.get("setup_type", "unknown")
        bucket = by_setup.setdefault(st, {"setup_type": st, "wins": 0, "losses": 0, "pnl": Decimal("0")})
        pnl = Decimal(e["realized_pnl"])
        bucket["pnl"] += pnl
        if pnl > 0:
            bucket["wins"] += 1
        elif pnl < 0:
            bucket["losses"] += 1
    return [
        {**b, "pnl": format(b["pnl"], "f")} for b in by_setup.values()
    ]


def compute_decision_summary(
    paths: AccountPaths,
    *,
    since: datetime,
    until: datetime,
) -> dict[str, Any]:
    """Aggregate decision-log activity in window."""
    if not paths.decisions.is_file():
        return {
            "total_decisions": 0, "skips": 0, "entries": 0,
            "closes": 0, "modifies": 0, "top_skip_reasons": [],
        }
    recs = [
        r for r in filter_decisions(paths.decisions, since=since)
        if _tick_within(r.get("tick_id"), since, until)
    ]
    skip_reasons = [r["reasoning"] for r in recs if r["kind"] == "skip"]
    counter: dict[str, int] = {}
    for reason in skip_reasons:
        counter[reason] = counter.get(reason, 0) + 1
    top = sorted(counter.items(), key=lambda kv: -kv[1])[:5]
    return {
        "total_decisions": len(recs),
        "skips": sum(1 for r in recs if r["kind"] == "skip"),
        "entries": sum(1 for r in recs if r["kind"] == "open"),
        "closes": sum(1 for r in recs if r["kind"] == "close"),
        "modifies": sum(1 for r in recs if r["kind"] == "modify"),
        "top_skip_reasons": top,
    }


def _tick_within(tick: Optional[str], since: datetime, until: datetime) -> bool:
    if not tick:
        return False
    dt = datetime.fromisoformat(tick.replace("Z", "+00:00"))
    return since <= dt < until
