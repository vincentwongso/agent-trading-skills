"""Tests for the per-TF and overall ScanContext composition."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from cfd_skills.indicators import Bar
from cfd_skills.price_action.bars import MTFBars
from cfd_skills.price_action.context import (
    ScanContext,
    TFContext,
    build_context,
)


def _bar(i: int, c: str = "100.0") -> Bar:
    cd = Decimal(c)
    return Bar(
        time_utc=datetime(2026, 4, 1, tzinfo=timezone.utc) + timedelta(hours=i),
        open=cd, high=cd + Decimal("1"), low=cd - Decimal("1"), close=cd,
        volume=0,
    )


def test_build_context_populates_per_tf_blocks() -> None:
    # 60 bars so ema50 + atr14 produce non-None values (spec used 40 — fixed
    # per task instructions: ema50 needs >=50 bars).
    bars_h1 = [_bar(i, c=str(100 + i)) for i in range(60)]
    bars_h4 = [_bar(i, c=str(100 + i)) for i in range(60)]
    mtf = MTFBars(bars_by_tf={"H1": bars_h1, "H4": bars_h4})
    ctx = build_context(
        symbol="XAUUSD",
        mtf=mtf,
        current_price=Decimal("139"),
        tick_size=Decimal("0.01"),
        digits=2,
        cluster_factor=20,
        pivot_lookback=3,
        displacement_atr_mult=Decimal("1.5"),
    )
    assert ctx.symbol == "XAUUSD"
    assert "H4" in ctx.tfs
    h4 = ctx.tfs["H4"]
    assert isinstance(h4, TFContext)
    assert h4.regime in {"trend_up", "trend_down", "range", "transition"}
    assert h4.ema21 is not None and h4.ema50 is not None


def test_build_context_handles_sparse_tf() -> None:
    bars = [_bar(i) for i in range(5)]
    mtf = MTFBars(bars_by_tf={"H1": bars})
    ctx = build_context(
        symbol="XAUUSD", mtf=mtf,
        current_price=Decimal("100"),
        tick_size=Decimal("0.01"), digits=2,
        cluster_factor=20, pivot_lookback=3,
        displacement_atr_mult=Decimal("1.5"),
    )
    h1 = ctx.tfs["H1"]
    assert h1.ema21 is None
    assert h1.ema50 is None
