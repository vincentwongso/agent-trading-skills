"""Tests for ``cfd_skills.risk_state`` — Position dataclass + risk math."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from cfd_skills.risk_state import (
    Classification,
    Position,
    at_risk_loss,
    cash_at_risk,
    drawdown_to_sl,
    has_no_stop,
    position_risk_pct,
)


# ---------- Fixture factories ----------------------------------------------


def _xauusd_symbol(**overrides):
    blob = {
        "name": "XAUUSD",
        "tick_size": "0.01",
        "tick_value": "1.00",  # USD account: $1 per tick per lot
    }
    blob.update(overrides)
    return blob


def _ukoil_symbol(**overrides):
    blob = {
        "name": "UKOIL",
        "tick_size": "0.01",
        "tick_value": "1.00",
    }
    blob.update(overrides)
    return blob


def _position_blob(**overrides):
    base = {
        "ticket": 1234567,
        "symbol": "XAUUSD",
        "side": "long",
        "volume": "0.5",
        "price_open": "2400.00",
        "sl": "2390.00",
        "tp": "2450.00",
        "price_current": "2410.00",
        "profit": "500.00",
        "swap": "12.50",
        "open_time": "2026-04-28T10:00:00+00:00",
    }
    base.update(overrides)
    return base


def _build(symbol_overrides=None, position_overrides=None, classification=None, reason=""):
    return Position.from_mcp(
        position=_position_blob(**(position_overrides or {})),
        symbol=_xauusd_symbol(**(symbol_overrides or {})),
        classification=classification,
        classification_reason=reason,
    )


# ---------- Position.from_mcp -----------------------------------------------


def test_from_mcp_parses_long_position() -> None:
    p = _build()
    assert p.ticket == 1234567
    assert p.side == "long"
    assert p.volume == Decimal("0.5")
    assert p.entry_price == Decimal("2400.00")
    assert p.sl == Decimal("2390.00")
    assert p.tp == Decimal("2450.00")
    assert p.classification == Classification.AT_RISK
    assert p.classification_reason == ""


def test_from_mcp_defaults_classification_to_at_risk() -> None:
    """Critical: false-positive RISK_FREE is the dangerous error."""
    p = _build(classification=None)
    assert p.classification == Classification.AT_RISK


def test_from_mcp_accepts_classification_override_with_reason() -> None:
    p = _build(
        classification="RISK_FREE",
        reason="SL trailed to entry; structure intact above 2400.",
    )
    assert p.classification == Classification.RISK_FREE
    assert "SL trailed" in p.classification_reason


def test_from_mcp_uppercases_classification() -> None:
    p = _build(classification="locked_profit")
    assert p.classification == Classification.LOCKED_PROFIT


def test_from_mcp_treats_zero_sl_as_no_stop() -> None:
    """mt5 reports unset SL as 0; treat as None to skip drawdown calc."""
    p = _build(position_overrides={"sl": "0"})
    assert p.sl is None
    assert has_no_stop(p) is True


def test_from_mcp_rejects_unknown_side() -> None:
    with pytest.raises(ValueError, match="unsupported side"):
        Position.from_mcp(
            position=_position_blob(side="hedge"),
            symbol=_xauusd_symbol(),
        )


def test_from_mcp_rejects_float_inputs() -> None:
    """decimal_io.D rejects floats — schema must catch them at the boundary."""
    with pytest.raises(TypeError, match="floats"):
        Position.from_mcp(
            position=_position_blob(volume=0.5),  # float, not str
            symbol=_xauusd_symbol(),
        )


def test_from_mcp_accepts_short_side() -> None:
    p = _build(position_overrides={
        "side": "short", "price_open": "2400.00",
        "sl": "2410.00", "price_current": "2395.00",
    })
    assert p.side == "short"
    assert p.sl == Decimal("2410.00")


def test_from_mcp_parses_open_time_iso() -> None:
    p = _build(position_overrides={"open_time": "2026-04-28T10:00:00+00:00"})
    assert p.open_time_utc.tzinfo is not None
    assert p.open_time_utc == datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc)


# ---------- drawdown_to_sl -------------------------------------------------


def test_drawdown_long_with_sl_below_entry_is_positive() -> None:
    """SL 1000 ticks below entry (10.00 / 0.01 tick), 0.5 lot, $1/tick = $500."""
    p = _build()  # entry 2400, sl 2390 → 1000 ticks down × 0.5 × $1 = $500
    assert drawdown_to_sl(p) == Decimal("500.00")


def test_drawdown_long_with_sl_above_entry_is_negative() -> None:
    """SL trailed above entry → drawdown negative (profit locked)."""
    p = _build(position_overrides={"price_open": "2400.00", "sl": "2410.00"})
    # 1000 ticks UP × 0.5 × $1 = $500 locked profit
    assert drawdown_to_sl(p) == Decimal("-500.00")


def test_drawdown_short_with_sl_above_entry_is_positive() -> None:
    p = _build(position_overrides={
        "side": "short", "price_open": "2400.00",
        "sl": "2410.00", "price_current": "2395.00",
    })
    # short: SL above entry = loss. 1000 ticks × 0.5 × $1 = $500 loss
    assert drawdown_to_sl(p) == Decimal("500.00")


def test_drawdown_short_with_sl_below_entry_is_negative() -> None:
    p = _build(position_overrides={
        "side": "short", "price_open": "2400.00",
        "sl": "2380.00", "price_current": "2395.00",
    })
    # short: SL below entry = profit locked. 2000 ticks × 0.5 × $1 = $1000
    assert drawdown_to_sl(p) == Decimal("-1000.00")


def test_drawdown_returns_none_when_no_sl() -> None:
    p = _build(position_overrides={"sl": "0"})
    assert drawdown_to_sl(p) is None


# ---------- cash_at_risk + at_risk_loss + position_risk_pct ----------------


def test_cash_at_risk_floors_locked_profit_to_zero() -> None:
    p = _build(position_overrides={"sl": "2410.00"})  # locked-in profit
    assert cash_at_risk(p) == Decimal("0")


def test_cash_at_risk_returns_loss_when_at_risk() -> None:
    p = _build()  # SL below entry
    assert cash_at_risk(p) == Decimal("500.00")


def test_at_risk_loss_zero_when_classification_is_risk_free() -> None:
    """RISK_FREE positions don't consume the daily cap."""
    p = _build(classification="RISK_FREE")
    assert at_risk_loss(p) == Decimal("0")


def test_at_risk_loss_zero_when_classification_is_locked_profit() -> None:
    p = _build(classification="LOCKED_PROFIT")
    assert at_risk_loss(p) == Decimal("0")


def test_at_risk_loss_returns_drawdown_when_at_risk() -> None:
    p = _build()
    assert at_risk_loss(p) == Decimal("500.00")


def test_position_risk_pct_against_10k_equity() -> None:
    p = _build()  # $500 drawdown
    pct = position_risk_pct(p, equity=Decimal("10000"))
    assert pct == Decimal("5.0")


def test_position_risk_pct_zero_for_zero_equity() -> None:
    p = _build()
    assert position_risk_pct(p, equity=Decimal("0")) == Decimal("0")


def test_position_risk_pct_zero_when_risk_free() -> None:
    p = _build(classification="RISK_FREE")
    assert position_risk_pct(p, equity=Decimal("10000")) == Decimal("0")
