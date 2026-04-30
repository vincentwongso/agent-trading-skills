"""Tests for ``trading_agent_skills.checklist`` — pre-trade gating verdict."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading_agent_skills.checklist import (
    CalixEarningsEntry,
    CalixEconomicEvent,
    ChecklistInput,
    SPREAD_WARN_RATIO,
    SymbolContext,
    assess,
)
from trading_agent_skills.config_io import default_config
from trading_agent_skills.guardian import GuardianResult
from trading_agent_skills.risk_state import Position
from trading_agent_skills.spread_baseline import Baseline


# ---------- Builders -------------------------------------------------------


def _guardian(
    *,
    status: str = "CLEAR",
    worst_case_pct: str = "0",
    concurrent_pct: str = "0",
) -> GuardianResult:
    return GuardianResult(
        status=status,
        now_utc="2026-04-29T21:00:00+00:00",
        last_reset_utc="2026-04-29T20:00:00+00:00",
        next_reset_utc="2026-04-30T20:00:00+00:00",
        seconds_until_next_reset=86400,
        deposit_currency="USD",
        equity=Decimal("10000"),
        session_open_balance=Decimal("10000"),
        realized_pnl_today=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        at_risk_combined_drawdown=Decimal("0"),
        worst_case_loss=Decimal("0"),
        worst_case_loss_pct_of_session=Decimal(worst_case_pct),
        daily_loss_cap_pct=Decimal("5.0"),
        caution_threshold_pct=Decimal("2.5"),
        concurrent_risk_budget_pct=Decimal("5.0"),
        concurrent_risk_consumed_pct=Decimal(concurrent_pct),
        positions=[],
        flags=[],
        notes=[],
    )


def _ctx(
    *,
    symbol: str = "XAUUSD",
    base: str = "XAU",
    profit: str = "USD",
    category: str = "metals",
    market_open: bool = True,
) -> SymbolContext:
    return SymbolContext(
        symbol=symbol,
        currency_base=base,
        currency_profit=profit,
        category=category,
        market_open=market_open,
    )


def _input(**overrides) -> ChecklistInput:
    base = dict(
        symbol_ctx=_ctx(),
        side="long",
        candidate_risk_pct=None,
        guardian=_guardian(),
        economic_events=[],
        earnings_entries=[],
        economic_stale=False,
        earnings_stale=False,
        existing_positions=[],
        current_spread_pts=None,
        spread_baseline=None,
        now_utc=datetime(2026, 4, 29, 21, 0, tzinfo=timezone.utc),
        config=default_config().risk,
    )
    base.update(overrides)
    return ChecklistInput(**base)


def _xauusd_pos(
    *,
    ticket: int = 1,
    symbol: str = "XAUUSD",
) -> Position:
    return Position.from_mcp(
        position={
            "ticket": ticket,
            "symbol": symbol,
            "side": "long",
            "volume": "0.5",
            "price_open": "2400.00",
            "sl": "2390.00",
            "tp": "2450.00",
            "price_current": "2400.00",
            "profit": "0",
            "swap": "0",
            "open_time": "2026-04-29T20:30:00+00:00",
        },
        symbol={"name": symbol, "tick_size": "0.01", "tick_value": "1.00"},
    )


def _by(name, checks):
    return next(c for c in checks if c.name == name)


# ---------- Top-level verdict aggregation ----------------------------------


def test_all_passes_returns_pass() -> None:
    result = assess(_input())
    assert result.verdict == "PASS"
    statuses = {c.name: c.status for c in result.checks}
    assert all(s == "PASS" for s in statuses.values()), statuses


def test_any_block_yields_block() -> None:
    """Closed market is BLOCK; verdict must be BLOCK regardless of others."""
    result = assess(_input(symbol_ctx=_ctx(market_open=False)))
    assert result.verdict == "BLOCK"


def test_any_warn_yields_warn_when_no_block() -> None:
    """Stale calix → news WARN, others PASS."""
    result = assess(_input(economic_stale=True))
    assert result.verdict == "WARN"


# ---------- daily_risk -----------------------------------------------------


def test_daily_risk_halt_propagates_to_block() -> None:
    result = assess(_input(guardian=_guardian(status="HALT", worst_case_pct="6.0")))
    daily = _by("daily_risk", result.checks)
    assert daily.status == "BLOCK"
    assert result.verdict == "BLOCK"


def test_daily_risk_caution_propagates_to_warn() -> None:
    result = assess(_input(guardian=_guardian(status="CAUTION", worst_case_pct="2.5")))
    daily = _by("daily_risk", result.checks)
    assert daily.status == "WARN"


# ---------- concurrent_budget ----------------------------------------------


def test_no_candidate_risk_passes_concurrent_check() -> None:
    result = assess(_input(candidate_risk_pct=None))
    assert _by("concurrent_budget", result.checks).status == "PASS"


def test_candidate_within_budget_passes() -> None:
    g = _guardian(concurrent_pct="2.0")
    result = assess(_input(guardian=g, candidate_risk_pct=Decimal("1.0")))
    chk = _by("concurrent_budget", result.checks)
    assert chk.status == "PASS"
    assert chk.detail["projected_pct"] == "3.0"


def test_candidate_pushes_over_budget_warns() -> None:
    g = _guardian(concurrent_pct="4.5")
    result = assess(_input(guardian=g, candidate_risk_pct=Decimal("1.0")))
    chk = _by("concurrent_budget", result.checks)
    assert chk.status == "WARN"
    assert chk.detail["projected_pct"] == "5.5"


# ---------- news_proximity -------------------------------------------------


def test_high_impact_event_within_30min_warns() -> None:
    now = datetime(2026, 4, 29, 21, 0, tzinfo=timezone.utc)
    evt = CalixEconomicEvent(
        title="FOMC Statement",
        currency="USD",
        impact="High",
        scheduled_at_utc=now + timedelta(minutes=15),
    )
    result = assess(_input(economic_events=[evt], now_utc=now))
    chk = _by("news_proximity", result.checks)
    assert chk.status == "WARN"
    assert "FOMC Statement" in chk.reason


def test_event_for_irrelevant_currency_does_not_warn() -> None:
    """JPY event for a XAUUSD trade is irrelevant."""
    now = datetime(2026, 4, 29, 21, 0, tzinfo=timezone.utc)
    evt = CalixEconomicEvent(
        title="BoJ Rate Decision",
        currency="JPY",
        impact="High",
        scheduled_at_utc=now + timedelta(minutes=15),
    )
    result = assess(_input(economic_events=[evt], now_utc=now))
    assert _by("news_proximity", result.checks).status == "PASS"


def test_event_outside_30min_window_does_not_warn() -> None:
    now = datetime(2026, 4, 29, 21, 0, tzinfo=timezone.utc)
    evt = CalixEconomicEvent(
        title="FOMC Statement",
        currency="USD",
        impact="High",
        scheduled_at_utc=now + timedelta(minutes=45),
    )
    result = assess(_input(economic_events=[evt], now_utc=now))
    assert _by("news_proximity", result.checks).status == "PASS"


def test_calix_stale_warns_news_check() -> None:
    result = assess(_input(economic_stale=True))
    chk = _by("news_proximity", result.checks)
    assert chk.status == "WARN"
    assert "stale" in chk.reason.lower()


def test_calix_event_blob_round_trips() -> None:
    """from_blob must accept the actual Calix shape (Z-suffix datetime)."""
    blob = {
        "id": "evt-1",
        "title": "CPI",
        "currency": "USD",
        "impact": "High",
        "scheduledAt": "2026-04-29T21:15:00Z",
        "forecast": "3.1%",
        "previous": "3.0%",
    }
    evt = CalixEconomicEvent.from_blob(blob)
    assert evt.scheduled_at_utc == datetime(2026, 4, 29, 21, 15, tzinfo=timezone.utc)


# ---------- earnings_proximity ---------------------------------------------


def test_earnings_check_skipped_for_non_index() -> None:
    result = assess(_input())  # XAUUSD is not an index
    chk = _by("earnings_proximity", result.checks)
    assert chk.status == "PASS"
    assert chk.detail["applicable"] is False


def test_earnings_today_warns_for_index() -> None:
    earnings = [
        CalixEarningsEntry(symbol="AAPL", scheduled_date="2026-04-29", timing="amc"),
    ]
    result = assess(_input(
        symbol_ctx=_ctx(symbol="NAS100", base="USD", profit="USD", category="indices"),
        earnings_entries=earnings,
    ))
    chk = _by("earnings_proximity", result.checks)
    assert chk.status == "WARN"
    assert "AAPL" in chk.reason


def test_earnings_other_day_passes_for_index() -> None:
    earnings = [
        CalixEarningsEntry(symbol="AAPL", scheduled_date="2026-04-30", timing="amc"),
    ]
    result = assess(_input(
        symbol_ctx=_ctx(symbol="NAS100", base="USD", profit="USD", category="indices"),
        earnings_entries=earnings,
    ))
    assert _by("earnings_proximity", result.checks).status == "PASS"


# ---------- session --------------------------------------------------------


def test_market_closed_blocks() -> None:
    result = assess(_input(symbol_ctx=_ctx(market_open=False)))
    chk = _by("session", result.checks)
    assert chk.status == "BLOCK"
    assert "closed" in chk.reason.lower()


# ---------- exposure_overlap -----------------------------------------------


def test_existing_same_symbol_position_warns() -> None:
    p = _xauusd_pos(ticket=1, symbol="XAUUSD")
    result = assess(_input(existing_positions=[p]))
    chk = _by("exposure_overlap", result.checks)
    assert chk.status == "WARN"
    assert chk.detail["same_symbol_tickets"] == [1]


def test_correlated_currency_position_warns() -> None:
    """XAUUSD entry while EURUSD long is open → shared USD exposure."""
    p = _xauusd_pos(ticket=99, symbol="EURUSD")
    result = assess(_input(existing_positions=[p]))
    chk = _by("exposure_overlap", result.checks)
    assert chk.status == "WARN"
    assert chk.detail["correlated_tickets"] == [99]


def test_unrelated_position_passes() -> None:
    p = _xauusd_pos(ticket=99, symbol="GER40")
    result = assess(_input(existing_positions=[p]))
    chk = _by("exposure_overlap", result.checks)
    assert chk.status == "PASS"


# ---------- spread ---------------------------------------------------------


def test_spread_within_baseline_passes() -> None:
    baseline = Baseline(
        symbol="XAUUSD",
        ewma=Decimal("10"),
        samples=20,
        updated_utc=datetime(2026, 4, 29, 20, 0, tzinfo=timezone.utc),
    )
    result = assess(_input(current_spread_pts=Decimal("12"), spread_baseline=baseline))
    assert _by("spread", result.checks).status == "PASS"


def test_spread_above_2x_baseline_warns() -> None:
    baseline = Baseline(
        symbol="XAUUSD",
        ewma=Decimal("10"),
        samples=20,
        updated_utc=datetime(2026, 4, 29, 20, 0, tzinfo=timezone.utc),
    )
    result = assess(_input(current_spread_pts=Decimal("25"), spread_baseline=baseline))
    chk = _by("spread", result.checks)
    assert chk.status == "WARN"
    assert chk.detail["ratio"] == "2.5"
    assert chk.detail["warn_threshold"] == format(SPREAD_WARN_RATIO, "f")


def test_spread_no_baseline_passes_with_note() -> None:
    result = assess(_input(current_spread_pts=Decimal("12")))
    chk = _by("spread", result.checks)
    assert chk.status == "PASS"
    assert "bootstrap" in chk.reason.lower()


def test_spread_no_current_passes_silently() -> None:
    """If the agent didn't supply current spread, just skip."""
    result = assess(_input())
    assert _by("spread", result.checks).status == "PASS"


# ---------- earnings_stale flag --------------------------------------------


def test_earnings_stale_flag_only_for_indices() -> None:
    result = assess(_input(
        symbol_ctx=_ctx(symbol="XAUUSD"),
        earnings_stale=True,
    ))
    assert "EARNINGS_DATA_STALE" not in result.flags


def test_earnings_stale_flag_set_for_index() -> None:
    result = assess(_input(
        symbol_ctx=_ctx(symbol="NAS100", base="USD", profit="USD", category="indices"),
        earnings_stale=True,
    ))
    assert "EARNINGS_DATA_STALE" in result.flags
