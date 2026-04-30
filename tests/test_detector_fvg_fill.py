"""Tests for the FVG fill detector."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading_agent_skills.indicators import Bar
from trading_agent_skills.price_action.bars import MTFBars
from trading_agent_skills.price_action.context import build_context
from trading_agent_skills.price_action.detectors.fvg_fill import detect


def _bar(i: int, o: str, h: str, l: str, c: str) -> Bar:
    return Bar(
        time_utc=datetime(2026, 4, 1, tzinfo=timezone.utc) + timedelta(hours=i),
        open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c),
        volume=0,
    )


def test_fvg_fill_long_when_price_in_demand_fvg() -> None:
    bars = []
    for i in range(20):
        bars.append(_bar(i, "98", "99", "97", "98"))
    bars.append(_bar(20, "98", "100", "98", "99.5"))
    bars.append(_bar(21, "99.5", "103", "99.5", "102.5"))
    bars.append(_bar(22, "102.5", "104", "102", "103.5"))
    for i in range(23, 30):
        bars.append(_bar(i, "103", "104", "102.5", "103"))
    bars.append(_bar(30, "103", "103", "101.5", "102.7"))
    mtf = MTFBars(bars_by_tf={"H4": bars, "H1": bars})
    ctx = build_context(
        symbol="XAUUSD", mtf=mtf,
        current_price=bars[-1].close,
        tick_size=Decimal("0.01"), digits=2,
        cluster_factor=20, pivot_lookback=3,
        displacement_atr_mult=Decimal("1.5"),
    )
    cands = detect(ctx)
    assert any(c.side == "long" and c.type == "fvg_fill" for c in cands)


def test_fvg_fill_no_fvg_no_signal() -> None:
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
