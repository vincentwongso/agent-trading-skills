"""Tests for ``cfd_skills.price_action.bars`` — MTF wrapper around indicators.Bar."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from cfd_skills.indicators import Bar
from cfd_skills.price_action.bars import (
    MTFBars,
    Timeframe,
    parse_timeframe,
    timeframe_minutes,
)


def _bar(t: datetime, c: str = "100.0") -> Bar:
    cd = Decimal(c)
    return Bar(
        time_utc=t,
        open=cd,
        high=cd + Decimal("1.0"),
        low=cd - Decimal("1.0"),
        close=cd,
        volume=0,
    )


def _series(tf: Timeframe, n: int = 5) -> list[Bar]:
    base = datetime(2026, 4, 1, tzinfo=timezone.utc)
    delta = timedelta(minutes=timeframe_minutes(tf))
    return [_bar(base + delta * i, c=str(100 + i)) for i in range(n)]


def test_parse_timeframe_accepts_canonical_codes() -> None:
    for code in ("M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"):
        assert parse_timeframe(code) == code


def test_parse_timeframe_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unknown timeframe"):
        parse_timeframe("M2")


def test_timeframe_minutes_known_codes() -> None:
    assert timeframe_minutes("M5") == 5
    assert timeframe_minutes("H1") == 60
    assert timeframe_minutes("H4") == 240
    assert timeframe_minutes("D1") == 1440


def test_mtfbars_from_bundle_builds_per_tf_lists() -> None:
    bundle = {
        "H4": [
            {"time": "2026-04-01T00:00:00+00:00", "open": "100", "high": "101",
             "low": "99", "close": "100.5", "volume": 0},
        ],
        "H1": [
            {"time": "2026-04-01T00:00:00+00:00", "open": "100", "high": "101",
             "low": "99", "close": "100.5", "volume": 0},
        ],
    }
    mtf = MTFBars.from_bundle(bundle)
    assert mtf.timeframes() == ("H1", "H4")  # sorted ascending by minutes
    assert len(mtf.bars("H4")) == 1
    assert mtf.bars("H4")[0].close == Decimal("100.5")


def test_mtfbars_recent_returns_last_n() -> None:
    mtf = MTFBars(bars_by_tf={"H1": _series("H1", n=10)})
    last3 = mtf.recent("H1", 3)
    assert len(last3) == 3
    assert last3[-1].close == Decimal("109")


def test_mtfbars_recent_clamps_when_n_exceeds() -> None:
    mtf = MTFBars(bars_by_tf={"H1": _series("H1", n=4)})
    assert len(mtf.recent("H1", 99)) == 4


def test_mtfbars_missing_tf_raises() -> None:
    mtf = MTFBars(bars_by_tf={"H1": _series("H1")})
    with pytest.raises(KeyError):
        mtf.bars("D1")
