"""Tests for ``trading_agent_skills.price_action.pivots`` — fractal pivot detection."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading_agent_skills.indicators import Bar
from trading_agent_skills.price_action.pivots import (
    Pivot,
    PivotKind,
    classify_sequence,
    detect_pivots,
)


def _bar(i: int, h: str, l: str) -> Bar:
    base = datetime(2026, 4, 1, tzinfo=timezone.utc)
    return Bar(
        time_utc=base + timedelta(hours=i),
        open=Decimal(h),
        high=Decimal(h),
        low=Decimal(l),
        close=Decimal(h),
        volume=0,
    )


def _series(spec: list[tuple[str, str]]) -> list[Bar]:
    """spec: list of (high, low) string pairs, oldest first."""
    return [_bar(i, h, l) for i, (h, l) in enumerate(spec)]


def test_detect_pivots_basic_swing_high_and_low() -> None:
    # ascending then descending → pivot high in the middle
    bars = _series([
        ("100", "99"), ("101", "100"), ("105", "103"),  # rising into peak
        ("104", "102"), ("103", "101"),                  # falling from peak
        ("102", "100"), ("101", "99"),                   # continuing down
    ])
    pivots = detect_pivots(bars, lookback=2)
    # Expect a swing_high at index 2 (high=105)
    highs = [p for p in pivots if p.kind == "swing_high"]
    assert len(highs) == 1
    assert highs[0].index == 2
    assert highs[0].price == Decimal("105")


def test_detect_pivots_skips_unconfirmed_at_edges() -> None:
    bars = _series([("100", "99"), ("101", "100"), ("102", "101")])
    pivots = detect_pivots(bars, lookback=2)
    # Last two bars cannot be confirmed (lookforward window incomplete)
    assert all(p.index < len(bars) - 2 for p in pivots)


def test_detect_pivots_invalid_lookback() -> None:
    with pytest.raises(ValueError):
        detect_pivots([], lookback=0)


def test_classify_sequence_marks_hh_hl() -> None:
    pivots = [
        Pivot(index=0, time_utc=datetime(2026, 4, 1, tzinfo=timezone.utc),
              price=Decimal("100"), kind="swing_low"),
        Pivot(index=1, time_utc=datetime(2026, 4, 2, tzinfo=timezone.utc),
              price=Decimal("110"), kind="swing_high"),
        Pivot(index=2, time_utc=datetime(2026, 4, 3, tzinfo=timezone.utc),
              price=Decimal("105"), kind="swing_low"),
        Pivot(index=3, time_utc=datetime(2026, 4, 4, tzinfo=timezone.utc),
              price=Decimal("115"), kind="swing_high"),
    ]
    classified = classify_sequence(pivots)
    labels = [p.label for p in classified]
    # low(100) → no prior low → None
    # high(110) → no prior high → None
    # low(105) > 100 → HL
    # high(115) > 110 → HH
    assert labels == [None, None, "HL", "HH"]


def test_classify_sequence_marks_lh_ll() -> None:
    pivots = [
        Pivot(0, datetime(2026, 4, 1, tzinfo=timezone.utc), Decimal("110"), "swing_high"),
        Pivot(1, datetime(2026, 4, 2, tzinfo=timezone.utc), Decimal("100"), "swing_low"),
        Pivot(2, datetime(2026, 4, 3, tzinfo=timezone.utc), Decimal("108"), "swing_high"),
        Pivot(3, datetime(2026, 4, 4, tzinfo=timezone.utc), Decimal("95"), "swing_low"),
    ]
    classified = classify_sequence(pivots)
    assert [p.label for p in classified] == [None, None, "LH", "LL"]


def test_classify_sequence_empty() -> None:
    assert classify_sequence([]) == []
