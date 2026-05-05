"""Tests for ``trading_agent_skills.macro_context``."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from trading_agent_skills.macro_context import MacroContext, MacroReading, build_macro_context


def _indicator(
    name: str,
    values: list[tuple[str, str]],
) -> tuple[str, list[dict[str, str]]]:
    """Return (name, data_points) pair. values is [(date, value), ...] most-recent-first."""
    return name, [{"date": d, "value": v} for d, v in values]


def test_rising_direction() -> None:
    name, data = _indicator("CPI", [("2026-04-01", "315.0"), ("2026-03-01", "312.5")])
    ctx = build_macro_context({name: data}, reference_date=date(2026, 5, 1))
    assert len(ctx.readings) == 1
    assert ctx.readings[0].direction == "rising"
    assert ctx.readings[0].latest_value == Decimal("315.0")
    assert ctx.readings[0].previous_value == Decimal("312.5")


def test_falling_direction() -> None:
    name, data = _indicator("UNEMPLOYMENT", [("2026-04-01", "3.8"), ("2026-03-01", "4.0")])
    ctx = build_macro_context({name: data}, reference_date=date(2026, 5, 1))
    assert ctx.readings[0].direction == "falling"


def test_flat_direction() -> None:
    name, data = _indicator("FEDERAL_FUNDS_RATE", [("2026-05-01", "5.25"), ("2026-04-30", "5.25")])
    ctx = build_macro_context({name: data}, reference_date=date(2026, 5, 1))
    assert ctx.readings[0].direction == "flat"


def test_multiple_indicators() -> None:
    indicators = dict([
        _indicator("CPI", [("2026-04-01", "315.0"), ("2026-03-01", "312.5")]),
        _indicator("UNEMPLOYMENT", [("2026-04-01", "3.8"), ("2026-03-01", "4.0")]),
        _indicator("TREASURY_YIELD", [("2026-05-01", "4.35"), ("2026-04-30", "4.30")]),
    ])
    ctx = build_macro_context(indicators, reference_date=date(2026, 5, 1))
    assert len(ctx.readings) == 3
    names = {r.name for r in ctx.readings}
    assert names == {"CPI", "UNEMPLOYMENT", "TREASURY_YIELD"}


def test_stale_gdp_flagged() -> None:
    name, data = _indicator("REAL_GDP", [("2025-12-01", "22000"), ("2025-09-01", "21800")])
    ctx = build_macro_context({name: data}, reference_date=date(2026, 5, 1))
    assert "REAL_GDP" in ctx.staleness_flags
    assert len(ctx.readings) == 1


def test_fresh_cpi_not_flagged() -> None:
    name, data = _indicator("CPI", [("2026-04-15", "315.0"), ("2026-03-15", "312.5")])
    ctx = build_macro_context({name: data}, reference_date=date(2026, 5, 1))
    assert "CPI" not in ctx.staleness_flags


def test_stale_daily_indicator() -> None:
    name, data = _indicator("TREASURY_YIELD", [("2026-04-25", "4.35"), ("2026-04-24", "4.30")])
    ctx = build_macro_context({name: data}, reference_date=date(2026, 5, 1))
    assert "TREASURY_YIELD" in ctx.staleness_flags


def test_missing_value_dot_skipped() -> None:
    data = [
        {"date": "2026-04-01", "value": "."},
        {"date": "2026-03-01", "value": "312.5"},
        {"date": "2026-02-01", "value": "310.0"},
    ]
    ctx = build_macro_context({"CPI": data}, reference_date=date(2026, 5, 1))
    assert len(ctx.readings) == 1
    assert ctx.readings[0].latest_value == Decimal("312.5")
    assert ctx.readings[0].previous_value == Decimal("310.0")


def test_insufficient_data_produces_staleness_flag() -> None:
    data = [{"date": "2026-04-01", "value": "315.0"}]
    ctx = build_macro_context({"CPI": data}, reference_date=date(2026, 5, 1))
    assert len(ctx.readings) == 0
    assert "CPI" in ctx.staleness_flags


def test_empty_indicators_dict() -> None:
    ctx = build_macro_context({}, reference_date=date(2026, 5, 1))
    assert len(ctx.readings) == 0
    assert len(ctx.staleness_flags) == 0


def test_decimal_coercion_from_string() -> None:
    name, data = _indicator("CPI", [("2026-04-01", "315.123"), ("2026-03-01", "312.456")])
    ctx = build_macro_context({name: data}, reference_date=date(2026, 5, 1))
    reading = ctx.readings[0]
    assert isinstance(reading.latest_value, Decimal)
    assert isinstance(reading.previous_value, Decimal)
    assert reading.latest_value == Decimal("315.123")


def test_unknown_indicator_no_staleness_check() -> None:
    name, data = _indicator("CUSTOM_THING", [("2026-04-01", "100"), ("2026-03-01", "99")])
    ctx = build_macro_context({name: data}, reference_date=date(2026, 5, 1))
    assert len(ctx.readings) == 1
    assert "CUSTOM_THING" not in ctx.staleness_flags
