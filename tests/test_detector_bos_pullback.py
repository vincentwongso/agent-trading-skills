"""Tests for the BOS pullback detector."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from cfd_skills.indicators import Bar
from cfd_skills.price_action.bars import MTFBars
from cfd_skills.price_action.context import build_context
from cfd_skills.price_action.detectors.bos_pullback import detect


def _bar(i: int, o: str, h: str, l: str, c: str) -> Bar:
    return Bar(
        time_utc=datetime(2026, 4, 1, tzinfo=timezone.utc) + timedelta(hours=i),
        open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c),
        volume=0,
    )


def test_bos_pullback_long_after_break_then_retrace_to_swing() -> None:
    bars = []
    for i in range(20):
        bars.append(_bar(i, "100", "105", "99", "104"))
    bars.append(_bar(20, "104", "105", "100", "100.5"))
    for i in range(21, 40):
        bars.append(_bar(i, "101", "104", "100", "103"))
    bars.append(_bar(40, "103", "108", "103", "107.5"))
    bars.append(_bar(41, "107.5", "108", "105.05", "106"))
    mtf = MTFBars(bars_by_tf={"H4": bars, "H1": bars})
    ctx = build_context(
        symbol="XAUUSD", mtf=mtf,
        current_price=bars[-1].close,
        tick_size=Decimal("0.01"), digits=2,
        cluster_factor=20, pivot_lookback=3,
        displacement_atr_mult=Decimal("1.5"),
    )
    cands = detect(ctx)
    assert all(c.type == "bos_pullback" for c in cands)


def test_bos_pullback_no_signal_in_range() -> None:
    flat = [_bar(i, "100", "100.5", "99.5", "100") for i in range(60)]
    mtf = MTFBars(bars_by_tf={"H4": flat, "H1": flat})
    ctx = build_context(
        symbol="XAUUSD", mtf=mtf,
        current_price=Decimal("100"),
        tick_size=Decimal("0.01"), digits=2,
        cluster_factor=20, pivot_lookback=3,
        displacement_atr_mult=Decimal("1.5"),
    )
    assert detect(ctx) == []
