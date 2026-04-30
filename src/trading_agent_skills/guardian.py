"""Daily-risk-guardian orchestrator — pure function over pre-fetched state.

The agent assembles a JSON bundle from ``get_account_info`` (equity),
``get_history`` (today's closed deals → realized P&L summed in deposit ccy),
``get_positions`` × ``get_symbols`` (open positions enriched with
classification reasoning), and ``daily_state.tick(...)`` (session-open
balance + reset timing). The CLI pipes that bundle here.

Status logic:
  - HALT if the worst-case loss % (today's realized + sum of AT_RISK drawdowns
    to SL, divided by session_open_balance) exceeds the configured cap.
  - CAUTION if the worst-case loss % crosses the caution threshold, OR if
    the concurrent at-risk budget (sum of per-AT_RISK-position risk %) exceeds
    its configured cap.
  - CLEAR otherwise.

This module is read-only: it never executes orders. Output is advisory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from trading_agent_skills.config_io import RiskConfig
from trading_agent_skills.decimal_io import D
from trading_agent_skills.risk_state import (
    Classification,
    Position,
    at_risk_loss,
    drawdown_to_sl,
    has_no_stop,
    position_risk_pct,
)


# 22h ≈ "held across a swap roll" — exact swap math lives in swap_calc and is
# beyond this skill's remit. The flag is informational, not a verdict input.
_OVERNIGHT_THRESHOLD = timedelta(hours=22)


# ---------- Inputs ---------------------------------------------------------


@dataclass(frozen=True)
class AccountSnapshot:
    equity: Decimal
    balance: Decimal
    currency: str

    @classmethod
    def from_mcp(cls, blob: dict[str, Any]) -> "AccountSnapshot":
        return cls(
            equity=D(blob["equity"]),
            balance=D(blob["balance"]),
            currency=str(blob["currency"]),
        )


@dataclass(frozen=True)
class GuardianInput:
    now_utc: datetime
    account: AccountSnapshot
    session_open_balance: Decimal
    last_reset_utc: datetime
    next_reset_utc: datetime
    realized_pnl_today: Decimal
    positions: list[Position]
    config: RiskConfig


# ---------- Outputs --------------------------------------------------------


@dataclass
class PositionSummary:
    ticket: int
    symbol: str
    side: str
    volume: Decimal
    classification: str
    classification_reason: str
    drawdown_to_sl: Optional[Decimal]   # signed, deposit ccy
    risk_pct_of_equity: Decimal          # 0 for non-AT_RISK
    unrealized_pnl: Decimal
    swap_accrued: Decimal
    is_overnight: bool                   # crossed at least one swap-roll
    has_no_stop: bool


@dataclass
class GuardianResult:
    status: str                                          # "CLEAR" / "CAUTION" / "HALT"
    now_utc: str
    last_reset_utc: str
    next_reset_utc: str
    seconds_until_next_reset: int
    deposit_currency: str
    equity: Decimal
    session_open_balance: Decimal
    realized_pnl_today: Decimal
    unrealized_pnl: Decimal
    at_risk_combined_drawdown: Decimal                  # positive = adverse
    worst_case_loss: Decimal                             # positive = adverse
    worst_case_loss_pct_of_session: Decimal              # positive = adverse
    daily_loss_cap_pct: Decimal
    caution_threshold_pct: Decimal
    concurrent_risk_budget_pct: Decimal
    concurrent_risk_consumed_pct: Decimal
    positions: list[PositionSummary] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---------- Core logic -----------------------------------------------------


def _is_overnight(open_time_utc: datetime, now_utc: datetime) -> bool:
    if open_time_utc.tzinfo is None:
        open_time_utc = open_time_utc.replace(tzinfo=timezone.utc)
    return (now_utc - open_time_utc) >= _OVERNIGHT_THRESHOLD


def _summarise(p: Position, equity: Decimal, now_utc: datetime) -> PositionSummary:
    return PositionSummary(
        ticket=p.ticket,
        symbol=p.symbol,
        side=p.side,
        volume=p.volume,
        classification=p.classification.value,
        classification_reason=p.classification_reason,
        drawdown_to_sl=drawdown_to_sl(p),
        risk_pct_of_equity=position_risk_pct(p, equity),
        unrealized_pnl=p.unrealized_pnl,
        swap_accrued=p.swap_accrued,
        is_overnight=_is_overnight(p.open_time_utc, now_utc),
        has_no_stop=has_no_stop(p),
    )


def assess(inp: GuardianInput) -> GuardianResult:
    """Compute today's risk verdict + per-position breakdown."""
    cfg = inp.config
    flags: list[str] = []
    notes: list[str] = []

    if inp.session_open_balance <= 0:
        # Defensive — session_open should always be positive (broker equity).
        notes.append(
            "Session-open balance is non-positive; loss-pct math would divide "
            "by zero. Reporting CLEAR but treat as data quality issue."
        )
        flags.append("INVALID_SESSION_OPEN_BALANCE")

    summaries = [_summarise(p, inp.account.equity, inp.now_utc) for p in inp.positions]

    at_risk_combined = sum(
        (at_risk_loss(p) for p in inp.positions), start=Decimal("0")
    )
    unrealized = sum(
        (p.unrealized_pnl for p in inp.positions), start=Decimal("0")
    )

    # realized_pnl_today is signed (negative = lost). Subtracting at_risk_combined
    # because that's the additional cash we'd lose if all SLs hit.
    worst_case_pnl = inp.realized_pnl_today - at_risk_combined
    worst_case_loss = -worst_case_pnl  # positive Decimal == loss
    if inp.session_open_balance > 0:
        worst_case_pct = worst_case_loss / inp.session_open_balance * Decimal("100")
    else:
        worst_case_pct = Decimal("0")

    cap = cfg.daily_loss_cap_pct
    caution_threshold = cap * cfg.caution_threshold_pct_of_cap / Decimal("100")
    concurrent_consumed = sum(
        (position_risk_pct(p, inp.account.equity) for p in inp.positions),
        start=Decimal("0"),
    )

    status = "CLEAR"
    if worst_case_pct >= cap:
        status = "HALT"
        flags.append("DAILY_CAP_BREACHED")
        notes.append(
            f"Worst-case loss {worst_case_pct:.2f}% of session-open meets/exceeds "
            f"the {cap}% daily cap — no new entries today."
        )
    elif worst_case_pct >= caution_threshold:
        status = "CAUTION"
        flags.append("DAILY_CAP_CAUTION")
        notes.append(
            f"Worst-case loss {worst_case_pct:.2f}% has crossed the caution "
            f"threshold ({caution_threshold:.2f}% = {cfg.caution_threshold_pct_of_cap}% "
            f"of the {cap}% cap)."
        )

    if concurrent_consumed > cfg.concurrent_risk_budget_pct:
        if status == "CLEAR":
            status = "CAUTION"
        flags.append("CONCURRENT_BUDGET_BREACHED")
        notes.append(
            f"Concurrent at-risk budget {concurrent_consumed:.2f}% exceeds the "
            f"{cfg.concurrent_risk_budget_pct}% configured cap. "
            "Trail SLs to breakeven on existing trades or skip the next entry."
        )

    if any(s.has_no_stop and Classification(s.classification) == Classification.AT_RISK
           for s in summaries):
        flags.append("AT_RISK_POSITION_HAS_NO_STOP")
        notes.append(
            "One or more AT_RISK positions have no stop-loss set. "
            "Worst-case math undercounts their downside; set a stop."
        )

    if any(s.is_overnight for s in summaries):
        flags.append("OVERNIGHT_FINANCING")
        notes.append(
            "Position(s) held across a swap roll — see swap_accrued per position. "
            "Wednesdays charge 3x for most instruments."
        )

    return GuardianResult(
        status=status,
        now_utc=inp.now_utc.isoformat(),
        last_reset_utc=inp.last_reset_utc.isoformat(),
        next_reset_utc=inp.next_reset_utc.isoformat(),
        seconds_until_next_reset=int((inp.next_reset_utc - inp.now_utc).total_seconds()),
        deposit_currency=inp.account.currency,
        equity=inp.account.equity,
        session_open_balance=inp.session_open_balance,
        realized_pnl_today=inp.realized_pnl_today,
        unrealized_pnl=unrealized,
        at_risk_combined_drawdown=at_risk_combined,
        worst_case_loss=worst_case_loss,
        worst_case_loss_pct_of_session=worst_case_pct,
        daily_loss_cap_pct=cap,
        caution_threshold_pct=caution_threshold,
        concurrent_risk_budget_pct=cfg.concurrent_risk_budget_pct,
        concurrent_risk_consumed_pct=concurrent_consumed,
        positions=summaries,
        flags=flags,
        notes=notes,
    )


__all__ = [
    "AccountSnapshot",
    "GuardianInput",
    "PositionSummary",
    "GuardianResult",
    "assess",
]
