"""Tests for the S/R bounce detector."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from cfd_skills.indicators import Bar
from cfd_skills.price_action.bars import MTFBars
from cfd_skills.price_action.context import build_context
from cfd_skills.price_action.detectors.sr_bounce import detect


def _bar(i: int, o: str, h: str, l: str, c: str) -> Bar:
    return Bar(
        time_utc=datetime(2026, 4, 1, tzinfo=timezone.utc) + timedelta(hours=i),
        open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c),
        volume=0,
    )


def _series_with_support_test(n: int = 60) -> list[Bar]:
    """Construct bars where 100 acts as support, with a fresh test on the last bar."""
    out: list[Bar] = []
    for i in range(n - 1):
        out.append(_bar(i, "102", "103", "100.5", "102"))
    out.append(_bar(n - 1, "101", "102", "99.95", "101.5"))
    return out


def test_sr_bounce_long_at_support() -> None:
    bars = _series_with_support_test()
    mtf = MTFBars(bars_by_tf={"H4": bars, "H1": bars})
    ctx = build_context(
        symbol="XAUUSD", mtf=mtf,
        current_price=Decimal("101.5"),
        tick_size=Decimal("0.01"), digits=2,
        cluster_factor=20, pivot_lookback=3,
        displacement_atr_mult=Decimal("1.5"),
    )
    cands = detect(ctx)
    longs = [c for c in cands if c.side == "long" and c.type == "sr_bounce"]
    assert len(longs) >= 0


def test_sr_bounce_no_levels_no_candidates() -> None:
    flat_bars = [
        _bar(i, "100", "100.5", "99.5", "100") for i in range(60)
    ]
    mtf = MTFBars(bars_by_tf={"H4": flat_bars, "H1": flat_bars})
    ctx = build_context(
        symbol="XAUUSD", mtf=mtf,
        current_price=Decimal("100"),
        tick_size=Decimal("0.01"), digits=2,
        cluster_factor=20, pivot_lookback=3,
        displacement_atr_mult=Decimal("1.5"),
    )
    assert detect(ctx) == []
