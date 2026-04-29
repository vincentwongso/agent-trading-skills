"""Tests for ``cfd_skills.price_action.fvg`` — three-bar FVG detection."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from cfd_skills.indicators import Bar
from cfd_skills.price_action.fvg import FVG, detect_fvgs


def _bar(i: int, o: str, h: str, l: str, c: str) -> Bar:
    return Bar(
        time_utc=datetime(2026, 4, 1, tzinfo=timezone.utc) + timedelta(hours=i),
        open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c),
        volume=0,
    )


def test_detect_fvgs_bullish_demand_gap_unfilled() -> None:
    bars = [
        _bar(0, "99", "100", "98", "99.5"),
        _bar(1, "99.5", "103", "99.5", "102.5"),
        _bar(2, "102.5", "104", "102", "103.5"),
    ]
    fvgs = detect_fvgs(bars, tf="H1")
    assert len(fvgs) == 1
    fvg = fvgs[0]
    assert fvg.side == "demand"
    assert fvg.low == Decimal("100")
    assert fvg.high == Decimal("102")
    assert fvg.filled_pct == Decimal("0")


def test_detect_fvgs_bearish_supply_gap_unfilled() -> None:
    bars = [
        _bar(0, "101", "102", "100", "100.5"),
        _bar(1, "100.5", "100.5", "97", "97.5"),
        _bar(2, "97.5", "98", "96", "96.5"),
    ]
    fvgs = detect_fvgs(bars, tf="H1")
    assert len(fvgs) == 1
    fvg = fvgs[0]
    assert fvg.side == "supply"
    assert fvg.low == Decimal("98")
    assert fvg.high == Decimal("100")


def test_detect_fvgs_partial_fill_demand() -> None:
    bars = [
        _bar(0, "99", "100", "98", "99.5"),
        _bar(1, "99.5", "103", "99.5", "102.5"),
        _bar(2, "102.5", "104", "102", "103.5"),
        _bar(3, "103.5", "104", "101", "101.5"),
    ]
    fvgs = detect_fvgs(bars, tf="H1")
    fvg = next(f for f in fvgs if f.side == "demand")
    assert fvg.filled_pct == Decimal("0.5")


def test_detect_fvgs_fully_filled_demand_excluded() -> None:
    bars = [
        _bar(0, "99", "100", "98", "99.5"),
        _bar(1, "99.5", "103", "99.5", "102.5"),
        _bar(2, "102.5", "104", "102", "103.5"),
        _bar(3, "103.5", "104", "99.5", "99.7"),
    ]
    fvgs = detect_fvgs(bars, tf="H1")
    assert all(f.side != "demand" for f in fvgs)


def test_detect_fvgs_no_gap() -> None:
    bars = [
        _bar(0, "100", "101", "99", "100.5"),
        _bar(1, "100.5", "101.5", "100", "101"),
        _bar(2, "101", "102", "100.5", "101.5"),
    ]
    assert detect_fvgs(bars, tf="H1") == []


def test_detect_fvgs_handles_short_series() -> None:
    assert detect_fvgs([], tf="H1") == []
    assert detect_fvgs([_bar(0, "100", "101", "99", "100.5")], tf="H1") == []
