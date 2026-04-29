"""Tests for the range-break + retest detector."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from cfd_skills.indicators import Bar
from cfd_skills.price_action.bars import MTFBars
from cfd_skills.price_action.context import build_context
from cfd_skills.price_action.detectors.range_break_retest import detect


def _bar(i: int, o: str, h: str, l: str, c: str) -> Bar:
    return Bar(
        time_utc=datetime(2026, 4, 1, tzinfo=timezone.utc) + timedelta(hours=i),
        open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c),
        volume=0,
    )


def test_range_break_long_after_retest() -> None:
    bars = []
    for i in range(50):
        bars.append(_bar(i, "101", "102", "100", "101"))
    bars.append(_bar(50, "101", "103.5", "101", "103.4"))
    bars.append(_bar(51, "103.4", "103.4", "102.05", "103"))
    mtf = MTFBars(bars_by_tf={"H4": bars, "H1": bars})
    ctx = build_context(
        symbol="XAUUSD", mtf=mtf,
        current_price=bars[-1].close,
        tick_size=Decimal("0.01"), digits=2,
        cluster_factor=20, pivot_lookback=3,
        displacement_atr_mult=Decimal("1.5"),
    )
    cands = detect(ctx)
    assert any(c.side == "long" and c.type == "range_break_retest" for c in cands)


def test_range_break_no_signal_without_retest() -> None:
    bars = []
    for i in range(40):
        bars.append(_bar(i, "100", "100.3", "99.8", "100"))
    mtf = MTFBars(bars_by_tf={"H4": bars, "H1": bars})
    ctx = build_context(
        symbol="XAUUSD", mtf=mtf,
        current_price=Decimal("100"),
        tick_size=Decimal("0.01"), digits=2,
        cluster_factor=20, pivot_lookback=3,
        displacement_atr_mult=Decimal("1.5"),
    )
    assert detect(ctx) == []
