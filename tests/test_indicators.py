"""Tests for ``trading_agent_skills.indicators`` — Wilder ATR/RSI + standard EMA."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading_agent_skills.indicators import (
    Bar,
    InsufficientBars,
    atr,
    bars_from_mcp,
    ema,
    rsi,
    snapshot,
    true_ranges,
)


# ---------- Bar fixture ----------------------------------------------------


def _bar(
    *,
    o: str, h: str, l: str, c: str, v: int = 0,
    t: datetime | None = None,
) -> Bar:
    return Bar(
        time_utc=t or datetime(2026, 4, 1, tzinfo=timezone.utc),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(l),
        close=Decimal(c),
        volume=v,
    )


def _series(closes: list[str], *, hl_spread: str = "1.0") -> list[Bar]:
    """Build a synthetic bar series with stable HL spread around each close."""
    spread = Decimal(hl_spread)
    out: list[Bar] = []
    base = datetime(2026, 4, 1, tzinfo=timezone.utc)
    for i, c in enumerate(closes):
        cd = Decimal(c)
        out.append(_bar(
            o=str(cd),
            h=str(cd + spread),
            l=str(cd - spread),
            c=str(cd),
            t=base + timedelta(days=i),
        ))
    return out


# ---------- Bar.from_mcp ---------------------------------------------------


def test_bar_from_mcp_parses_iso_time() -> None:
    blob = {
        "time": "2026-04-29T00:00:00+00:00",
        "open": "100.00", "high": "101.50", "low": "99.50",
        "close": "101.00", "volume": 10000,
    }
    b = Bar.from_mcp(blob)
    assert b.time_utc == datetime(2026, 4, 29, tzinfo=timezone.utc)
    assert b.high == Decimal("101.50")


def test_bars_from_mcp_round_trip() -> None:
    blobs = [{"time": "2026-04-29T00:00:00Z",
              "open": "100", "high": "101", "low": "99", "close": "100.5"}]
    bars = bars_from_mcp(blobs)
    assert len(bars) == 1
    assert bars[0].close == Decimal("100.5")


def test_bar_from_mcp_rejects_floats() -> None:
    with pytest.raises(TypeError, match="floats"):
        Bar.from_mcp({"time": "2026-04-29T00:00:00Z",
                       "open": 100.0, "high": "101", "low": "99", "close": "100"})


# ---------- true_ranges ----------------------------------------------------


def test_true_ranges_first_bar_uses_high_minus_low() -> None:
    bars = [
        _bar(o="100", h="105", l="95", c="102"),
        _bar(o="102", h="106", l="100", c="103"),
    ]
    trs = true_ranges(bars)
    assert trs[0] == Decimal("10")  # 105 - 95
    # Second bar: max(106-100=6, |106-102|=4, |100-102|=2) = 6
    assert trs[1] == Decimal("6")


def test_true_ranges_uses_prev_close_gap() -> None:
    bars = [
        _bar(o="100", h="101", l="99", c="100"),
        _bar(o="105", h="106", l="104", c="105"),  # gap up
    ]
    trs = true_ranges(bars)
    # Second TR: max(106-104=2, |106-100|=6, |104-100|=4) = 6
    assert trs[1] == Decimal("6")


def test_true_ranges_empty_returns_empty() -> None:
    assert true_ranges([]) == []


# ---------- ATR ------------------------------------------------------------


def test_atr_constant_range_equals_constant() -> None:
    """Constant H-L = 2.0 across 20 bars → ATR = 2.0."""
    bars = _series([str(100 + i) for i in range(20)], hl_spread="1.0")
    # First TR is HL spread = 2; subsequent TRs include gaps of 1.0 (close-to-close)
    # so TR = max(2, |new_high - prev_close|, ...). Let's compute exactly.
    # Bar i (i>0): close[i]=100+i, close[i-1]=99+i. high[i]=101+i, low[i]=99+i.
    # TR = max(2, |101+i - (99+i)|=2, |99+i - (99+i)|=0) = 2.
    result = atr(bars, period=14)
    assert result == Decimal("2")


def test_atr_raises_when_insufficient_bars() -> None:
    bars = _series([str(100 + i) for i in range(13)])
    with pytest.raises(InsufficientBars):
        atr(bars, period=14)


def test_atr_smoothing_picks_up_volatility_burst() -> None:
    """ATR after 14 calm bars + 1 wide bar should be > calm baseline."""
    closes = [str(100 + i) for i in range(15)]
    bars = _series(closes, hl_spread="1.0")
    # Mutate last bar to a wide range.
    last = bars[-1]
    bars[-1] = Bar(
        time_utc=last.time_utc,
        open=last.open,
        high=last.close + Decimal("10"),
        low=last.close - Decimal("10"),
        close=last.close,
    )
    a = atr(bars, period=14)
    assert a > Decimal("2")


def test_atr_zero_period_rejected() -> None:
    with pytest.raises(ValueError):
        atr(_series(["100"] * 15), period=0)


# ---------- RSI ------------------------------------------------------------


def test_rsi_all_up_returns_100() -> None:
    bars = _series([str(100 + i) for i in range(20)])
    assert rsi(bars, period=14) == Decimal("100")


def test_rsi_all_down_returns_zero() -> None:
    bars = _series([str(150 - i) for i in range(20)])
    assert rsi(bars, period=14) == Decimal("0")


def test_rsi_steady_returns_50() -> None:
    """No moves at all → no gain, no loss → RSI defaults to 50."""
    bars = _series(["100"] * 20)
    assert rsi(bars, period=14) == Decimal("50")


def test_rsi_known_series() -> None:
    """Hand-checked: 14 alternating ups (+1, +0) starting from 100.
    Closes: 100, 101, 101, 102, 102, 103, 103, 104, 104, 105, 105, 106, 106, 107, 107
    Gains:        1,   0,   1,   0,   1,   0,   1,   0,   1,   0,   1,   0,   1,   0
    Avg gain = 7/14 = 0.5, avg loss = 0 → RSI = 100.
    """
    closes = [str(100 + (i + 1) // 2) for i in range(15)]
    bars = _series(closes)
    assert rsi(bars, period=14) == Decimal("100")


def test_rsi_raises_when_insufficient_bars() -> None:
    bars = _series([str(100 + i) for i in range(14)])  # only 14, need 15
    with pytest.raises(InsufficientBars):
        rsi(bars, period=14)


def test_rsi_oversold_after_drawdown() -> None:
    """A long downtrend → RSI < 30 (oversold)."""
    closes = [str(200 - i * 2) for i in range(20)]
    r = rsi(closes_to_bars(closes), period=14)
    assert r < Decimal("30")


def closes_to_bars(closes: list[str]) -> list[Bar]:
    return _series(closes)


# ---------- EMA ------------------------------------------------------------


def test_ema_constant_series_equals_constant() -> None:
    bars = _series(["100"] * 25)
    assert ema(bars, period=20) == Decimal("100")


def test_ema_seeds_from_sma_when_exactly_period_bars() -> None:
    """closes 1..20 → SMA = 10.5, no smoothing applied since len == period."""
    bars = _series([str(i) for i in range(1, 21)])
    e = ema(bars, period=20)
    assert e == Decimal("10.5")


def test_ema_lags_after_step_change() -> None:
    """20 bars at 100 then 5 bars at 200 → EMA lifted but well below 200."""
    closes = ["100"] * 20 + ["200"] * 5
    bars = _series(closes)
    e = ema(bars, period=20)
    assert Decimal("100") < e < Decimal("200")


def test_ema_raises_when_insufficient_bars() -> None:
    with pytest.raises(InsufficientBars):
        ema(_series(["100"] * 19), period=20)


# ---------- snapshot -------------------------------------------------------


def test_snapshot_combines_all_three() -> None:
    """20 bars at 100 then 5 bars at 110 → RSI high, EMA pulled up, ATR finite."""
    bars = _series(["100"] * 20 + ["110"] * 5)
    s = snapshot("XAUUSD", bars)
    assert s.symbol == "XAUUSD"
    assert s.rsi_14 > Decimal("50")
    assert Decimal("100") < s.ema_20 < Decimal("110")
    assert s.atr_14 > 0
    assert s.last_close == Decimal("110")
    # Distance from EMA in ATR units > 0 (close above EMA).
    assert s.distance_from_ema_atr_units > 0


def test_snapshot_atr_pct_of_price_normalises() -> None:
    bars = _series([str(100 + i) for i in range(25)])
    s = snapshot("XAUUSD", bars)
    # ATR ~2, price ~124 → atr_pct ~ 1.6%
    assert Decimal("0.5") < s.atr_pct_of_price < Decimal("3")


def test_snapshot_empty_raises() -> None:
    with pytest.raises(InsufficientBars):
        snapshot("XAUUSD", [])
