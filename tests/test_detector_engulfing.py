"""Tests for the engulfing-at-level detector."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from cfd_skills.indicators import Bar
from cfd_skills.price_action.detectors.engulfing import is_bullish_engulfing, is_bearish_engulfing


def _bar(i: int, o: str, h: str, l: str, c: str) -> Bar:
    return Bar(
        time_utc=datetime(2026, 4, 1, tzinfo=timezone.utc) + timedelta(hours=i),
        open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c),
        volume=0,
    )


def test_is_bullish_engulfing_basic() -> None:
    prev = _bar(0, "101", "101.5", "99", "99.5")
    cur = _bar(1, "99.4", "102", "99.3", "101.8")
    assert is_bullish_engulfing(prev, cur) is True


def test_is_bearish_engulfing_basic() -> None:
    prev = _bar(0, "100", "101", "99.5", "100.5")
    cur = _bar(1, "100.6", "100.7", "98.5", "99")
    assert is_bearish_engulfing(prev, cur) is True


def test_is_bullish_engulfing_rejects_no_engulfment() -> None:
    prev = _bar(0, "101", "101.5", "99", "99.5")
    cur = _bar(1, "99.6", "100", "99.5", "99.8")
    assert is_bullish_engulfing(prev, cur) is False
