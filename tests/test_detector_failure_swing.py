"""Tests for the failure-swing detector (three pure pattern helpers)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading_agent_skills.indicators import Bar
from trading_agent_skills.price_action.detectors.failure_swing import (
    is_failure_swing,
    is_outside_day_reversal,
    is_three_bar_reversal,
)


def _bar(i: int, o: str, h: str, l: str, c: str) -> Bar:
    return Bar(
        time_utc=datetime(2026, 4, 1, tzinfo=timezone.utc) + timedelta(days=i),
        open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c),
        volume=0,
    )


# ---------- failure_swing -------------------------------------------------


def test_failure_swing_bearish_returns_short() -> None:
    # Five bars at ~100, then a new high spike, then close back below prior swing high.
    bars = [
        _bar(0, "100", "101", "99",  "100"),
        _bar(1, "100", "101", "99",  "100"),
        _bar(2, "100", "101", "99",  "100"),
        _bar(3, "100", "101", "99",  "100"),
        _bar(4, "100", "101", "99",  "100"),
        _bar(5, "100", "105", "100", "104"),  # new high
        _bar(6, "104", "104", "100", "100.5"),  # close back below 101 prior_high
        _bar(7, "100.5", "101", "99", "99.5"),  # final close still below prior_high (101)
    ]
    assert is_failure_swing(bars) == "short"


def test_failure_swing_bullish_returns_long() -> None:
    bars = [
        _bar(0, "100", "101", "99",  "100"),
        _bar(1, "100", "101", "99",  "100"),
        _bar(2, "100", "101", "99",  "100"),
        _bar(3, "100", "101", "99",  "100"),
        _bar(4, "100", "101", "99",  "100"),
        _bar(5, "100", "100", "95",  "96"),   # new low
        _bar(6, "96",  "100", "96",  "99.5"),  # close back above 99 prior_low
        _bar(7, "99.5", "101", "99.5", "100.5"),  # final close above prior_low (99)
    ]
    assert is_failure_swing(bars) == "long"


def test_failure_swing_no_new_extreme_returns_none() -> None:
    bars = [_bar(i, "100", "101", "99", "100") for i in range(8)]
    assert is_failure_swing(bars) is None


def test_failure_swing_returns_none_when_too_few_bars() -> None:
    assert is_failure_swing([_bar(0, "100", "101", "99", "100")]) is None


# ---------- outside_day_reversal ------------------------------------------


def test_outside_day_reversal_bearish() -> None:
    # 5 flat bars, then prev (101 close), then last bar takes out the 5-bar high
    # but closes below prev close → short.
    bars = [_bar(i, "100", "101", "99", "100") for i in range(5)]
    bars.append(_bar(5, "100", "101", "99", "101"))   # prev: close 101
    bars.append(_bar(6, "101", "105", "99", "100"))   # new high 105 > prior 101, close 100 < prev 101
    assert is_outside_day_reversal(bars) == "short"


def test_outside_day_reversal_bullish() -> None:
    bars = [_bar(i, "100", "101", "99", "100") for i in range(5)]
    bars.append(_bar(5, "100", "101", "99", "99"))    # prev: close 99
    bars.append(_bar(6, "99", "101", "95", "100"))    # new low 95 < prior 99, close 100 > prev 99
    assert is_outside_day_reversal(bars) == "long"


def test_outside_day_reversal_none_when_no_extreme_taken() -> None:
    bars = [_bar(i, "100", "101", "99", "100") for i in range(7)]
    assert is_outside_day_reversal(bars) is None


# ---------- three_bar_reversal --------------------------------------------


def test_three_bar_reversal_bearish() -> None:
    # Pivot (open 100, close 110) → three closes each below prior body midpoint.
    pivot = _bar(0, "100", "112", "100", "110")        # body mid 105
    b1 = _bar(1, "110", "111", "100", "104")            # 104 < 105 ✓ ; body mid 107
    b2 = _bar(2, "104", "105", "98",  "100")            # 100 < 107 ✓ ; body mid 102
    b3 = _bar(3, "100", "101", "95",  "96")             # 96  < 102 ✓
    assert is_three_bar_reversal([pivot, b1, b2, b3]) == "short"


def test_three_bar_reversal_bullish() -> None:
    pivot = _bar(0, "110", "110", "98",  "100")         # body mid 105
    b1 = _bar(1, "100", "112", "100", "110")            # 110 > 105 ✓ ; body mid 105
    b2 = _bar(2, "110", "115", "108", "114")            # 114 > 105 ✓ ; body mid 112
    b3 = _bar(3, "114", "120", "113", "118")            # 118 > 112 ✓
    assert is_three_bar_reversal([pivot, b1, b2, b3]) == "long"


def test_three_bar_reversal_none_on_choppy_bars() -> None:
    bars = [
        _bar(0, "100", "101", "99", "100"),
        _bar(1, "100", "101", "99", "100"),
        _bar(2, "100", "101", "99", "100"),
        _bar(3, "100", "101", "99", "100"),
    ]
    assert is_three_bar_reversal(bars) is None
