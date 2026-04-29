"""Tests for the pullback-to-EMA-stack detector."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from cfd_skills.indicators import Bar
from cfd_skills.price_action.bars import MTFBars
from cfd_skills.price_action.context import build_context
from cfd_skills.price_action.detectors.pullback_ema import detect


def _trend_up_bars(n: int = 80) -> list[Bar]:
    """Build an uptrend with 6-up / 3-down zigzags so fractal pivots form
    and the regime classifier sees enough HH/HL labels to return ``trend_up``.

    A monotonically rising series produces zero pivots (no local extrema)
    and falls through to ``regime=range``, which suppresses the detector —
    hence the zigzag pattern. Final bar is a sharp pullback that pierces
    EMA21 and closes back above it, satisfying the detector's trigger.
    """
    base = datetime(2026, 4, 1, tzinfo=timezone.utc)
    out: list[Bar] = []
    price = Decimal("100")
    i = 0
    while i < n:
        for _ in range(6):
            if i >= n:
                break
            price += Decimal("0.50")
            out.append(Bar(
                time_utc=base + timedelta(hours=i),
                open=price - Decimal("0.10"),
                high=price + Decimal("0.30"),
                low=price - Decimal("0.30"),
                close=price,
                volume=0,
            ))
            i += 1
        for _ in range(3):
            if i >= n:
                break
            price -= Decimal("0.40")
            out.append(Bar(
                time_utc=base + timedelta(hours=i),
                open=price + Decimal("0.10"),
                high=price + Decimal("0.30"),
                low=price - Decimal("0.30"),
                close=price,
                volume=0,
            ))
            i += 1
    last_close = out[-1].close
    out.append(Bar(
        time_utc=base + timedelta(hours=n),
        open=last_close,
        high=last_close,
        low=last_close - Decimal("3.00"),
        close=last_close - Decimal("1.50"),
        volume=0,
    ))
    return out


def test_pullback_ema_long_in_uptrend() -> None:
    bars = _trend_up_bars()
    mtf = MTFBars(bars_by_tf={"H4": bars, "H1": bars})
    ctx = build_context(
        symbol="XAUUSD", mtf=mtf,
        current_price=bars[-1].close,
        tick_size=Decimal("0.01"), digits=2,
        cluster_factor=20, pivot_lookback=3,
        displacement_atr_mult=Decimal("1.5"),
    )
    candidates = detect(ctx)
    assert any(c.side == "long" and c.type == "pullback_ema" for c in candidates)


def test_pullback_ema_no_signal_in_range() -> None:
    base = datetime(2026, 4, 1, tzinfo=timezone.utc)
    flat = [
        Bar(
            time_utc=base + timedelta(hours=i),
            open=Decimal("100"), high=Decimal("100.5"),
            low=Decimal("99.5"), close=Decimal("100"),
            volume=0,
        )
        for i in range(80)
    ]
    mtf = MTFBars(bars_by_tf={"H4": flat, "H1": flat})
    ctx = build_context(
        symbol="XAUUSD", mtf=mtf,
        current_price=Decimal("100"),
        tick_size=Decimal("0.01"), digits=2,
        cluster_factor=20, pivot_lookback=3,
        displacement_atr_mult=Decimal("1.5"),
    )
    candidates = detect(ctx)
    assert all(c.type != "pullback_ema" for c in candidates)
