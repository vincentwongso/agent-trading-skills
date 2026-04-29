"""Tests for ``cfd_skills.guardian.assess`` — daily risk verdict logic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from cfd_skills.config_io import default_config
from cfd_skills.guardian import (
    AccountSnapshot,
    GuardianInput,
    assess,
)
from cfd_skills.risk_state import Position


def _account(**overrides: Any) -> AccountSnapshot:
    blob = {"equity": "10000.00", "balance": "10000.00", "currency": "USD"}
    blob.update(overrides)
    return AccountSnapshot.from_mcp(blob)


def _xauusd_pos(
    *,
    classification: str | None = None,
    sl: str = "2390.00",
    entry: str = "2400.00",
    volume: str = "0.5",
    side: str = "long",
    profit: str = "0.00",
    swap: str = "0.00",
    open_time: str = "2026-04-29T20:30:00+00:00",
    ticket: int = 1,
) -> Position:
    return Position.from_mcp(
        position={
            "ticket": ticket,
            "symbol": "XAUUSD",
            "side": side,
            "volume": volume,
            "price_open": entry,
            "sl": sl,
            "tp": "2450.00",
            "price_current": "2400.00",
            "profit": profit,
            "swap": swap,
            "open_time": open_time,
        },
        symbol={"name": "XAUUSD", "tick_size": "0.01", "tick_value": "1.00"},
        classification=classification,
    )


def _input(
    *,
    realized: str = "0.00",
    positions: list[Position] | None = None,
    equity: str = "10000.00",
    session_open: str = "10000.00",
    now: datetime | None = None,
) -> GuardianInput:
    n = now or datetime(2026, 4, 29, 21, 0, tzinfo=timezone.utc)
    return GuardianInput(
        now_utc=n,
        account=_account(equity=equity),
        session_open_balance=Decimal(session_open),
        last_reset_utc=datetime(2026, 4, 29, 20, 0, tzinfo=timezone.utc),
        next_reset_utc=datetime(2026, 4, 30, 20, 0, tzinfo=timezone.utc),
        realized_pnl_today=Decimal(realized),
        positions=positions or [],
        config=default_config().risk,
    )


# ---------- CLEAR ----------------------------------------------------------


def test_no_positions_no_realized_returns_clear() -> None:
    result = assess(_input())
    assert result.status == "CLEAR"
    assert result.worst_case_loss_pct_of_session == Decimal("0")
    assert result.flags == []


def test_small_at_risk_position_within_caution_threshold() -> None:
    """0.1 lot on XAUUSD, $1/tick, 1000 ticks → $100 risk = 1% on $10k."""
    p = _xauusd_pos(volume="0.1")
    result = assess(_input(positions=[p]))
    assert result.status == "CLEAR"
    assert result.at_risk_combined_drawdown == Decimal("100.00")


# ---------- CAUTION --------------------------------------------------------


def test_loss_at_caution_threshold_returns_caution() -> None:
    """Realized -$250 on $10k → 2.5% loss, exactly the caution threshold."""
    result = assess(_input(realized="-250.00"))
    assert result.status == "CAUTION"
    assert "DAILY_CAP_CAUTION" in result.flags


def test_two_at_risk_positions_summing_over_cap_halt() -> None:
    """Two 0.3-lot AT_RISK positions = 6% combined drawdown on $10k → HALT."""
    p1 = _xauusd_pos(volume="0.3", ticket=1)
    p2 = _xauusd_pos(volume="0.3", ticket=2)
    result = assess(_input(positions=[p1, p2]))
    assert result.status == "HALT"
    # Note: with default config, daily_loss_cap_pct == concurrent_risk_budget_pct,
    # so worst-case-pct and concurrent-consumed-pct rise in lockstep. The
    # CONCURRENT-only CAUTION case is exercised in the next test with a tighter
    # concurrent budget.


def test_concurrent_budget_breach_caution_when_loss_under_cap() -> None:
    """Construct: 6 AT_RISK positions of $100 each on $20k account.
    Concurrent = 0.5% × 6 = 3% — wait that's under 5%.
    Try: 6 positions of $200 each on $20k. Each = 1% → 6% concurrent > 5% budget.
    Worst case = 1200/20000 = 6% > 5% cap. Still HALT.
    The only way to get CONCURRENT_BUDGET breach without DAILY_CAP breach is
    if some positions are RISK_FREE (excluded from worst-case but…wait,
    RISK_FREE excluded from BOTH per current logic). Let's verify the current
    logic actually allows this state."""
    # Per current logic: position_risk_pct returns 0 for non-AT_RISK,
    # AND at_risk_loss returns 0 for non-AT_RISK. So RISK_FREE doesn't add to
    # either bucket. That means concurrent_consumed and worst_case_pct are
    # ALWAYS proportional. The CONCURRENT_BUDGET_BREACHED flag can only fire
    # when worst_case_pct also breaches cap.
    # However: the *thresholds* differ (5% concurrent budget vs 5% daily cap);
    # they happen to match in defaults but the user could set them differently.
    # Test with config where concurrent_budget < cap to isolate.
    from dataclasses import replace as dc_replace

    cfg = default_config().risk
    tight = dc_replace(cfg, concurrent_risk_budget_pct=Decimal("3.0"))
    p1 = _xauusd_pos(volume="0.2", ticket=1)  # $200 = 2% on $10k
    p2 = _xauusd_pos(volume="0.2", ticket=2)  # $200 = 2% on $10k
    inp = GuardianInput(
        now_utc=datetime(2026, 4, 29, 21, 0, tzinfo=timezone.utc),
        account=_account(),
        session_open_balance=Decimal("10000.00"),
        last_reset_utc=datetime(2026, 4, 29, 20, 0, tzinfo=timezone.utc),
        next_reset_utc=datetime(2026, 4, 30, 20, 0, tzinfo=timezone.utc),
        realized_pnl_today=Decimal("0"),
        positions=[p1, p2],
        config=tight,
    )
    result = assess(inp)
    # Worst case 4% < 5% cap, but concurrent 4% > 3% tight budget → CAUTION
    assert result.status == "CAUTION"
    assert "CONCURRENT_BUDGET_BREACHED" in result.flags


# ---------- HALT -----------------------------------------------------------


def test_realized_loss_exceeds_cap_returns_halt() -> None:
    """Realized -$500 on $10k → exactly at cap → HALT."""
    result = assess(_input(realized="-500.00"))
    assert result.status == "HALT"
    assert "DAILY_CAP_BREACHED" in result.flags


def test_combined_realized_plus_at_risk_breaches_cap() -> None:
    """Realized -$200 + $400 AT_RISK drawdown = $600 worst-case = 6% > 5% cap."""
    p = _xauusd_pos(volume="0.4")  # 1000 ticks × $1 × 0.4 = $400
    result = assess(_input(realized="-200.00", positions=[p]))
    assert result.status == "HALT"


# ---------- RISK_FREE / LOCKED_PROFIT exclusion ----------------------------


def test_risk_free_position_does_not_consume_cap() -> None:
    """RISK_FREE position with SL below entry doesn't add to combined drawdown."""
    p = _xauusd_pos(classification="RISK_FREE")
    result = assess(_input(positions=[p]))
    assert result.at_risk_combined_drawdown == Decimal("0")
    assert result.status == "CLEAR"


def test_locked_profit_position_does_not_consume_cap() -> None:
    p = _xauusd_pos(classification="LOCKED_PROFIT", sl="2410.00")
    result = assess(_input(positions=[p]))
    assert result.at_risk_combined_drawdown == Decimal("0")


def test_position_risk_pct_breakdown_excludes_classified_safe() -> None:
    p1 = _xauusd_pos(classification="RISK_FREE", ticket=1)
    p2 = _xauusd_pos(classification="AT_RISK", volume="0.1", ticket=2)
    result = assess(_input(positions=[p1, p2]))
    # Only p2 contributes to concurrent risk.
    assert result.concurrent_risk_consumed_pct == Decimal("1.0")


# ---------- Per-position summaries -----------------------------------------


def test_position_summary_marks_overnight_when_open_22h() -> None:
    p = _xauusd_pos(open_time="2026-04-28T20:00:00+00:00")  # 25h before now
    result = assess(_input(positions=[p]))
    assert result.positions[0].is_overnight is True
    assert "OVERNIGHT_FINANCING" in result.flags


def test_position_summary_drawdown_signed_for_locked_profit() -> None:
    p = _xauusd_pos(classification="LOCKED_PROFIT", sl="2410.00")
    result = assess(_input(positions=[p]))
    # SL above entry on a long → negative drawdown (profit locked in)
    assert result.positions[0].drawdown_to_sl == Decimal("-500.00")


def test_at_risk_position_with_no_stop_flagged() -> None:
    p = _xauusd_pos(sl="0")
    result = assess(_input(positions=[p]))
    assert "AT_RISK_POSITION_HAS_NO_STOP" in result.flags


# ---------- Output shape ---------------------------------------------------


def test_result_carries_session_metadata() -> None:
    result = assess(_input())
    assert result.deposit_currency == "USD"
    assert result.daily_loss_cap_pct == Decimal("5.0")
    assert result.caution_threshold_pct == Decimal("2.50")
    assert result.concurrent_risk_budget_pct == Decimal("5.0")
    assert result.seconds_until_next_reset > 0


def test_zero_session_open_balance_flagged_not_crashed() -> None:
    inp = _input()
    inp = GuardianInput(
        now_utc=inp.now_utc,
        account=inp.account,
        session_open_balance=Decimal("0"),
        last_reset_utc=inp.last_reset_utc,
        next_reset_utc=inp.next_reset_utc,
        realized_pnl_today=inp.realized_pnl_today,
        positions=inp.positions,
        config=inp.config,
    )
    result = assess(inp)
    assert "INVALID_SESSION_OPEN_BALANCE" in result.flags
    assert result.worst_case_loss_pct_of_session == Decimal("0")
