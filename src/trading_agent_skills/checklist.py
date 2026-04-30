"""Pre-trade checklist orchestrator — pure function over pre-fetched data.

Each sub-check returns its own status (PASS / WARN / BLOCK) plus a short
reason string. The verdict aggregates as the strictest sub-status:

    BLOCK > WARN > PASS

Sub-checks:

  1. ``daily_risk``  — composes ``GuardianResult.status``.
  2. ``concurrent_budget`` — would adding ``candidate_risk_pct`` push the sum
     of AT_RISK position risk % over the configured cap?
  3. ``news_proximity`` — Calix economic events for the symbol's currencies
     within the next 30 minutes (``WARN_WINDOW_MINUTES``).
  4. ``earnings_proximity`` — for indices, Calix earnings on today's date.
  5. ``session`` — ``market_open`` must be ``True``.
  6. ``exposure_overlap`` — existing positions in the same symbol or in
     symbols that share a currency with the proposed entry.
  7. ``spread`` — current spread vs. EWMA baseline; > 2× baseline → WARN.

The skill never executes — output is informational. The agent renders it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable, Literal, Optional

from trading_agent_skills.config_io import RiskConfig
from trading_agent_skills.decimal_io import D
from trading_agent_skills.guardian import GuardianResult
from trading_agent_skills.risk_state import Position
from trading_agent_skills.spread_baseline import Baseline, ratio_vs_baseline
from trading_agent_skills.symbol_meta import _INDEX_TO_CURRENCIES, currencies_of_interest


WARN_WINDOW_MINUTES = 30
SPREAD_WARN_RATIO = Decimal("2.0")


CheckStatus = Literal["PASS", "WARN", "BLOCK"]
_RANK = {"PASS": 0, "WARN": 1, "BLOCK": 2}


def _strictest(statuses: Iterable[CheckStatus]) -> CheckStatus:
    rank = max((_RANK[s] for s in statuses), default=0)
    for k, v in _RANK.items():
        if v == rank:
            return k  # type: ignore[return-value]
    return "PASS"


# ---------- Sub-check result ------------------------------------------------


@dataclass
class CheckResult:
    name: str
    status: CheckStatus
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChecklistResult:
    verdict: CheckStatus
    symbol: str
    side: str
    checks: list[CheckResult] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---------- Inputs ---------------------------------------------------------


@dataclass(frozen=True)
class SymbolContext:
    symbol: str
    currency_base: str
    currency_profit: str
    category: str
    market_open: bool


@dataclass(frozen=True)
class CalixEconomicEvent:
    title: str
    currency: str
    impact: str
    scheduled_at_utc: datetime

    @classmethod
    def from_blob(cls, blob: dict[str, Any]) -> "CalixEconomicEvent":
        return cls(
            title=str(blob["title"]),
            currency=str(blob["currency"]),
            impact=str(blob["impact"]),
            scheduled_at_utc=datetime.fromisoformat(
                str(blob["scheduledAt"]).replace("Z", "+00:00")
            ),
        )


@dataclass(frozen=True)
class CalixEarningsEntry:
    symbol: str
    scheduled_date: str   # YYYY-MM-DD
    timing: str

    @classmethod
    def from_blob(cls, blob: dict[str, Any]) -> "CalixEarningsEntry":
        return cls(
            symbol=str(blob["symbol"]),
            scheduled_date=str(blob["scheduledDate"]),
            timing=str(blob.get("timing", "unknown")),
        )


# ---------- Sub-checks ------------------------------------------------------


def _check_daily_risk(g: GuardianResult) -> CheckResult:
    status_map: dict[str, CheckStatus] = {
        "CLEAR": "PASS",
        "CAUTION": "WARN",
        "HALT": "BLOCK",
    }
    status = status_map.get(g.status, "WARN")
    reason = (
        f"Guardian status: {g.status}. "
        f"Worst-case loss {g.worst_case_loss_pct_of_session:.2f}% "
        f"of {g.daily_loss_cap_pct}% cap."
    )
    return CheckResult(
        name="daily_risk",
        status=status,
        reason=reason,
        detail={
            "guardian_status": g.status,
            "worst_case_pct": format(g.worst_case_loss_pct_of_session, "f"),
            "daily_loss_cap_pct": format(g.daily_loss_cap_pct, "f"),
        },
    )


def _check_concurrent_budget(
    g: GuardianResult, candidate_risk_pct: Optional[Decimal], cfg: RiskConfig
) -> CheckResult:
    if candidate_risk_pct is None:
        return CheckResult(
            name="concurrent_budget",
            status="PASS",
            reason=(
                f"No candidate size supplied; current concurrent "
                f"{g.concurrent_risk_consumed_pct:.2f}% of "
                f"{cfg.concurrent_risk_budget_pct}% budget."
            ),
            detail={
                "current_concurrent_pct": format(g.concurrent_risk_consumed_pct, "f"),
                "budget_pct": format(cfg.concurrent_risk_budget_pct, "f"),
            },
        )
    candidate = D(candidate_risk_pct)
    projected = g.concurrent_risk_consumed_pct + candidate
    if projected > cfg.concurrent_risk_budget_pct:
        return CheckResult(
            name="concurrent_budget",
            status="WARN",
            reason=(
                f"Adding {candidate}% risk would push concurrent budget "
                f"to {projected:.2f}% (cap {cfg.concurrent_risk_budget_pct}%). "
                "Trail an existing SL to breakeven first or reduce size."
            ),
            detail={
                "current_concurrent_pct": format(g.concurrent_risk_consumed_pct, "f"),
                "candidate_pct": format(candidate, "f"),
                "projected_pct": format(projected, "f"),
                "budget_pct": format(cfg.concurrent_risk_budget_pct, "f"),
            },
        )
    return CheckResult(
        name="concurrent_budget",
        status="PASS",
        reason=(
            f"Projected concurrent {projected:.2f}% within "
            f"{cfg.concurrent_risk_budget_pct}% budget."
        ),
        detail={
            "current_concurrent_pct": format(g.concurrent_risk_consumed_pct, "f"),
            "candidate_pct": format(candidate, "f"),
            "projected_pct": format(projected, "f"),
            "budget_pct": format(cfg.concurrent_risk_budget_pct, "f"),
        },
    )


def _check_news_proximity(
    sym_ctx: SymbolContext,
    events: list[CalixEconomicEvent],
    *,
    now_utc: datetime,
    calix_stale: bool,
) -> CheckResult:
    relevant = currencies_of_interest(
        symbol=sym_ctx.symbol,
        currency_base=sym_ctx.currency_base,
        currency_profit=sym_ctx.currency_profit,
        category=sym_ctx.category,
    )
    window_end = now_utc + timedelta(minutes=WARN_WINDOW_MINUTES)
    in_window = [
        e
        for e in events
        if e.currency in relevant
        and now_utc <= e.scheduled_at_utc <= window_end
    ]
    if calix_stale:
        return CheckResult(
            name="news_proximity",
            status="WARN",
            reason=(
                "Calix calendar data is stale — cannot reliably check news "
                "proximity. Treat as if a high-impact event may be imminent."
            ),
            detail={"stale": True},
        )
    if in_window:
        titles = "; ".join(e.title for e in in_window[:3])
        return CheckResult(
            name="news_proximity",
            status="WARN",
            reason=(
                f"{len(in_window)} high-impact event(s) for {sorted(relevant)} "
                f"within {WARN_WINDOW_MINUTES} min: {titles}"
            ),
            detail={
                "events_in_window": [
                    {
                        "title": e.title,
                        "currency": e.currency,
                        "impact": e.impact,
                        "scheduled_at_utc": e.scheduled_at_utc.isoformat(),
                    }
                    for e in in_window
                ],
                "currencies_filtered": sorted(relevant),
            },
        )
    return CheckResult(
        name="news_proximity",
        status="PASS",
        reason=(
            f"No high-impact events for {sorted(relevant)} within "
            f"{WARN_WINDOW_MINUTES} min."
        ),
        detail={"currencies_filtered": sorted(relevant)},
    )


def _check_earnings_proximity(
    sym_ctx: SymbolContext,
    earnings: list[CalixEarningsEntry],
    *,
    now_utc: datetime,
) -> CheckResult:
    if sym_ctx.symbol.upper() not in _INDEX_TO_CURRENCIES:
        return CheckResult(
            name="earnings_proximity",
            status="PASS",
            reason="Not an index — earnings proximity not applicable.",
            detail={"applicable": False},
        )
    today = now_utc.date().isoformat()
    same_day = [e for e in earnings if e.scheduled_date == today]
    if same_day:
        names = ", ".join(e.symbol for e in same_day[:5])
        return CheckResult(
            name="earnings_proximity",
            status="WARN",
            reason=(
                f"{len(same_day)} index-constituent earnings reporting today: "
                f"{names}. Expect intraday volatility."
            ),
            detail={
                "today": today,
                "constituents_today": [
                    {"symbol": e.symbol, "timing": e.timing} for e in same_day
                ],
            },
        )
    return CheckResult(
        name="earnings_proximity",
        status="PASS",
        reason=f"No index-constituent earnings on {today}.",
        detail={"today": today},
    )


def _check_session(sym_ctx: SymbolContext) -> CheckResult:
    if not sym_ctx.market_open:
        return CheckResult(
            name="session",
            status="BLOCK",
            reason=f"{sym_ctx.symbol} market is closed.",
            detail={"market_open": False},
        )
    return CheckResult(
        name="session",
        status="PASS",
        reason=f"{sym_ctx.symbol} market is open.",
        detail={"market_open": True},
    )


def _check_exposure_overlap(
    sym_ctx: SymbolContext,
    existing_positions: list[Position],
) -> CheckResult:
    same_symbol = [p for p in existing_positions if p.symbol.upper() == sym_ctx.symbol.upper()]
    target_currencies = {sym_ctx.currency_base, sym_ctx.currency_profit}
    target_currencies.discard("")
    correlated: list[Position] = []
    for p in existing_positions:
        if p.symbol.upper() == sym_ctx.symbol.upper():
            continue
        # Heuristic: shared currency in the symbol name (e.g. XAUUSD vs EURUSD
        # both end in USD). True correlation matrix is a v2 enhancement.
        sym_upper = p.symbol.upper()
        if any(c and c in sym_upper for c in target_currencies):
            correlated.append(p)

    if same_symbol:
        tickets = ", ".join(str(p.ticket) for p in same_symbol)
        return CheckResult(
            name="exposure_overlap",
            status="WARN",
            reason=(
                f"Already have {len(same_symbol)} position(s) on {sym_ctx.symbol} "
                f"(tickets {tickets}). Stacking same-symbol exposure compounds risk."
            ),
            detail={
                "same_symbol_tickets": [p.ticket for p in same_symbol],
                "correlated_tickets": [p.ticket for p in correlated],
            },
        )
    if correlated:
        return CheckResult(
            name="exposure_overlap",
            status="WARN",
            reason=(
                f"{len(correlated)} existing position(s) share a currency with "
                f"{sym_ctx.symbol} ({sorted(target_currencies)}). "
                "Watch for compounded directional bias."
            ),
            detail={
                "same_symbol_tickets": [],
                "correlated_tickets": [p.ticket for p in correlated],
            },
        )
    return CheckResult(
        name="exposure_overlap",
        status="PASS",
        reason="No same-symbol or shared-currency positions open.",
        detail={"same_symbol_tickets": [], "correlated_tickets": []},
    )


def _check_spread(
    current_spread_pts: Optional[Decimal],
    baseline: Optional[Baseline],
) -> CheckResult:
    if current_spread_pts is None:
        return CheckResult(
            name="spread",
            status="PASS",
            reason="No current spread supplied — skipping spread baseline check.",
            detail={},
        )
    if baseline is None:
        return CheckResult(
            name="spread",
            status="PASS",
            reason=(
                f"Spread {current_spread_pts} pts; no prior baseline (will "
                "bootstrap on this sample)."
            ),
            detail={"current_pts": format(current_spread_pts, "f")},
        )
    ratio = ratio_vs_baseline(current_spread_pts, baseline)
    if ratio > SPREAD_WARN_RATIO:
        return CheckResult(
            name="spread",
            status="WARN",
            reason=(
                f"Spread {current_spread_pts} pts is {ratio:.2f}x the EWMA "
                f"baseline ({baseline.ewma:.2f} pts). Likely thin liquidity "
                "or news; cash risk is materially worse than the sizer estimates."
            ),
            detail={
                "current_pts": format(current_spread_pts, "f"),
                "baseline_pts": format(baseline.ewma, "f"),
                "ratio": format(ratio, "f"),
                "warn_threshold": format(SPREAD_WARN_RATIO, "f"),
            },
        )
    return CheckResult(
        name="spread",
        status="PASS",
        reason=(
            f"Spread {current_spread_pts} pts within {SPREAD_WARN_RATIO}x "
            f"baseline ({baseline.ewma:.2f})."
        ),
        detail={
            "current_pts": format(current_spread_pts, "f"),
            "baseline_pts": format(baseline.ewma, "f"),
            "ratio": format(ratio, "f"),
        },
    )


# ---------- Top-level ------------------------------------------------------


@dataclass(frozen=True)
class ChecklistInput:
    symbol_ctx: SymbolContext
    side: Literal["long", "short"]
    candidate_risk_pct: Optional[Decimal]
    guardian: GuardianResult
    economic_events: list[CalixEconomicEvent]
    earnings_entries: list[CalixEarningsEntry]
    economic_stale: bool
    earnings_stale: bool
    existing_positions: list[Position]
    current_spread_pts: Optional[Decimal]
    spread_baseline: Optional[Baseline]
    now_utc: datetime
    config: RiskConfig


def assess(inp: ChecklistInput) -> ChecklistResult:
    """Run all sub-checks and aggregate into a single PASS/WARN/BLOCK verdict."""
    now = inp.now_utc
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    checks = [
        _check_daily_risk(inp.guardian),
        _check_concurrent_budget(inp.guardian, inp.candidate_risk_pct, inp.config),
        _check_session(inp.symbol_ctx),
        _check_news_proximity(
            inp.symbol_ctx,
            inp.economic_events,
            now_utc=now,
            calix_stale=inp.economic_stale,
        ),
        _check_earnings_proximity(
            inp.symbol_ctx, inp.earnings_entries, now_utc=now
        ),
        _check_exposure_overlap(inp.symbol_ctx, inp.existing_positions),
        _check_spread(inp.current_spread_pts, inp.spread_baseline),
    ]

    verdict = _strictest(c.status for c in checks)

    flags: list[str] = []
    notes: list[str] = []
    if inp.earnings_stale and inp.symbol_ctx.symbol.upper() in _INDEX_TO_CURRENCIES:
        flags.append("EARNINGS_DATA_STALE")
        notes.append(
            "Calix earnings calendar is stale; index proximity check may miss "
            "a same-day reporter."
        )

    return ChecklistResult(
        verdict=verdict,
        symbol=inp.symbol_ctx.symbol,
        side=inp.side,
        checks=checks,
        flags=flags,
        notes=notes,
    )


__all__ = [
    "WARN_WINDOW_MINUTES",
    "SPREAD_WARN_RATIO",
    "CheckResult",
    "ChecklistResult",
    "SymbolContext",
    "CalixEconomicEvent",
    "CalixEarningsEntry",
    "ChecklistInput",
    "assess",
]
