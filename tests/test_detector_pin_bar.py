"""Tests for the pin bar detector."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading_agent_skills.indicators import Bar
from trading_agent_skills.price_action.bars import MTFBars
from trading_agent_skills.price_action.context import build_context
from trading_agent_skills.price_action.detectors.pin_bar import detect, is_pin_bar


def _bar(i: int, o: str, h: str, l: str, c: str) -> Bar:
    return Bar(
        time_utc=datetime(2026, 4, 1, tzinfo=timezone.utc) + timedelta(hours=i),
        open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c),
        volume=0,
    )


def test_is_pin_bar_bullish_long_lower_wick() -> None:
    b = _bar(0, "100", "100.3", "99", "100.2")
    side, ratio = is_pin_bar(b, min_wick_to_body=Decimal("2"))
    assert side == "bullish"
    assert ratio >= Decimal("2")


def test_is_pin_bar_bearish_long_upper_wick() -> None:
    b = _bar(0, "100", "101", "99.7", "99.8")
    side, ratio = is_pin_bar(b, min_wick_to_body=Decimal("2"))
    assert side == "bearish"


def test_is_pin_bar_no_signal_normal_candle() -> None:
    b = _bar(0, "100", "100.5", "99.5", "100.4")
    side, _ = is_pin_bar(b, min_wick_to_body=Decimal("2"))
    assert side is None


def test_detect_pin_bar_at_support_yields_long() -> None:
    base_bars = [_bar(i, "102", "103", "101", "102") for i in range(58)]
    base_bars.append(_bar(58, "101", "101.5", "99.95", "101"))
    base_bars.append(_bar(59, "101", "101.5", "100.05", "101.4"))
    mtf = MTFBars(bars_by_tf={"H4": base_bars, "H1": base_bars})
    ctx = build_context(
        symbol="XAUUSD", mtf=mtf,
        current_price=base_bars[-1].close,
        tick_size=Decimal("0.01"), digits=2,
        cluster_factor=20, pivot_lookback=3,
        displacement_atr_mult=Decimal("1.5"),
    )
    cands = detect(ctx)
    assert all(c.type == "pin_bar" for c in cands)
