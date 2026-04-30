"""Tests for the order-block retest detector."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading_agent_skills.indicators import Bar
from trading_agent_skills.price_action.bars import MTFBars
from trading_agent_skills.price_action.context import build_context
from trading_agent_skills.price_action.detectors.ob_retest import detect


def _bar(i: int, o: str, h: str, l: str, c: str) -> Bar:
    return Bar(
        time_utc=datetime(2026, 4, 1, tzinfo=timezone.utc) + timedelta(hours=i),
        open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c),
        volume=0,
    )


def test_ob_retest_long_after_demand_ob_retested() -> None:
    bars = []
    for i in range(20):
        bars.append(_bar(i, "100", "101", "99", "100"))
    bars.append(_bar(20, "99.8", "100", "98", "98.5"))
    bars.append(_bar(21, "98.5", "103", "98.5", "102.5"))
    bars.append(_bar(22, "102.5", "104", "102", "103.5"))
    bars.append(_bar(23, "103.5", "105", "103", "104.5"))
    bars.append(_bar(24, "104.5", "104.5", "99.5", "100"))
    mtf = MTFBars(bars_by_tf={"H4": bars, "H1": bars})
    ctx = build_context(
        symbol="XAUUSD", mtf=mtf,
        current_price=bars[-1].close,
        tick_size=Decimal("0.01"), digits=2,
        cluster_factor=20, pivot_lookback=3,
        displacement_atr_mult=Decimal("1.5"),
    )
    cands = detect(ctx)
    assert any(c.side == "long" and c.type == "ob_retest" for c in cands)


def test_ob_retest_no_signal_without_obs() -> None:
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
