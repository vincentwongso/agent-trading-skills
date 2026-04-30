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
from trading_agent_skills.charter_io import (
    LOCKED_FIELDS,
    Charter,
    HardCaps,
    parse_charter,
    render_charter,
    write_charter_with_archive,
)
from trading_agent_skills.decision_log import filter_decisions
from trading_agent_skills.journal_io import read_resolved


# All charter fields the user/agent could conceivably tune. Locked fields
# are excluded.
PROPOSABLE_FIELDS = frozenset({
    # hard_caps members (flattened for proposal-diff convenience)
    "per_trade_risk_pct", "daily_loss_pct", "max_concurrent_positions",
    # soft fields
    "trading_style", "heartbeat",
    "sessions_allowed", "instruments", "allowed_setups", "notes",
})


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


def validate_proposal_diff(diff: dict[str, Any]) -> None:
    """Raise ValueError if the diff touches any locked field or unknown field."""
    for key in diff.keys():
        if key in LOCKED_FIELDS:
            raise ValueError(f"field {key!r} is locked and cannot be proposed")
        if key not in PROPOSABLE_FIELDS:
            raise ValueError(f"field {key!r} is not a known proposable field")


def build_proposal_skeleton(
    paths: AccountPaths,
    *,
    since: datetime,
    until: datetime,
) -> str:
    """Emit a markdown skeleton the LLM fills in with judgements.

    The Python provides aggregated facts; the LLM provides analysis and
    diff proposals. The skeleton has placeholder sections for the diff
    so the LLM has clear slots to fill.
    """
    perf = compute_performance_summary(paths, since=since, until=until)
    by_setup = compute_setup_breakdown(paths, since=since, until=until)
    decisions = compute_decision_summary(paths, since=since, until=until)

    setup_lines = "\n".join(
        f"- {b['setup_type']}: {b['wins']}W / {b['losses']}L, P&L {b['pnl']}"
        for b in by_setup
    ) or "- (no closed trades in window)"

    skip_lines = "\n".join(
        f"- {reason}: {count}" for reason, count in decisions["top_skip_reasons"]
    ) or "- (no skips logged)"

    return f"""# Strategy review — {until.date().isoformat()}

## Performance summary ({since.date().isoformat()} → {until.date().isoformat()})

- Trades closed: {perf['trades_closed']} ({perf['wins']}W / {perf['losses']}L)
- Win rate: {perf['win_rate']}
- Realized P&L: {perf['realized_pnl']}

## Setup-type breakdown

{setup_lines}

## Decision-log analysis

- Total decisions: {decisions['total_decisions']}
- Entries: {decisions['entries']}, Closes: {decisions['closes']}, Modifies: {decisions['modifies']}, Skips: {decisions['skips']}
- Top skip reasons:

{skip_lines}

## Charter diff proposal (requires approval)

<!-- LLM: fill this section with concrete proposed changes based on the
     stats above. Use a YAML diff fence. Only fields in PROPOSABLE_FIELDS
     may appear. Locked fields are forbidden. -->

```diff
# (LLM-filled)
```

### Reasoning

<!-- LLM: explain the reasoning for each proposed change in 1-2 sentences. -->

## Reply with

- "approve all" — apply every change above
- "approve <fields>" — apply only listed (e.g., "approve per_trade_risk_pct, allowed_setups")
- "reject" — no changes; proposal archived as-is
- "discuss <topic>" — ask clarifying question
"""


def apply_proposal(
    paths: AccountPaths,
    *,
    approved_changes: dict[str, Any],
) -> Charter:
    """Apply approved field changes to the charter, bump version, archive prior.

    The new charter is round-tripped through render+parse before write,
    so out-of-bounds values (e.g. per_trade_risk_pct: 99.0) are rejected
    before they can corrupt the on-disk charter.
    """
    validate_proposal_diff(approved_changes)
    current = parse_charter(paths.charter.read_text(encoding="utf-8"))

    # Build the new charter from the current, overlaying approved changes.
    new_caps = HardCaps(
        per_trade_risk_pct=approved_changes.get("per_trade_risk_pct", current.hard_caps.per_trade_risk_pct),
        daily_loss_pct=approved_changes.get("daily_loss_pct", current.hard_caps.daily_loss_pct),
        max_concurrent_positions=approved_changes.get(
            "max_concurrent_positions", current.hard_caps.max_concurrent_positions
        ),
    )
    new_charter = Charter(
        mode=current.mode,
        account_id=current.account_id,
        heartbeat=approved_changes.get("heartbeat", current.heartbeat),
        hard_caps=new_caps,
        charter_version=current.charter_version + 1,
        created_at=current.created_at,
        created_account_balance=current.created_account_balance,
        trading_style=approved_changes.get("trading_style", current.trading_style),
        sessions_allowed=approved_changes.get("sessions_allowed", current.sessions_allowed),
        instruments=approved_changes.get("instruments", current.instruments),
        allowed_setups=approved_changes.get("allowed_setups", current.allowed_setups),
        notes=approved_changes.get("notes", current.notes),
    )
    # Round-trip validate before write — out-of-bounds risk pcts (e.g. 99.0)
    # would parse-fail and brick the next tick. Catch at write boundary instead.
    parse_charter(render_charter(new_charter))
    write_charter_with_archive(paths, new_charter)
    return new_charter
