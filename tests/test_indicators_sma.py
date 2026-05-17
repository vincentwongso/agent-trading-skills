"""Tests for the SMA helper added to ``indicators``."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading_agent_skills.indicators import Bar, InsufficientBars, sma


def _bar(i: int, c: str) -> Bar:
    return Bar(
        time_utc=datetime(2026, 4, 1, tzinfo=timezone.utc) + timedelta(days=i),
        open=Decimal(c), high=Decimal(c), low=Decimal(c), close=Decimal(c),
        volume=0,
    )


def test_sma_basic_average() -> None:
    bars = [_bar(i, str(c)) for i, c in enumerate([10, 12, 14, 16, 18])]
    assert sma(bars, period=5) == Decimal("14")


def test_sma_uses_only_trailing_period() -> None:
    # 10 bars, SMA(3) should use only last 3 (closes 80, 90, 100).
    closes = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    bars = [_bar(i, str(c)) for i, c in enumerate(closes)]
    assert sma(bars, period=3) == Decimal("90")


def test_sma_raises_on_insufficient_bars() -> None:
    bars = [_bar(0, "10")]
    with pytest.raises(InsufficientBars):
        sma(bars, period=5)


def test_sma_rejects_zero_period() -> None:
    with pytest.raises(ValueError):
        sma([_bar(0, "10")], period=0)
