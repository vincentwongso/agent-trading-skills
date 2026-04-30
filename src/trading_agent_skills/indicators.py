"""Wilder-smoothed ATR(14), RSI(14), and EMA(20) on Decimal-typed bars.

Used by ``session-news-brief`` to surface (a) volatility-ranked watchlist
candidates and (b) "swing candidates" — symbols sitting at an RSI extreme
that pay positive carry on the side that aligns with mean-reversion.

Why hand-rolled instead of pandas/numpy:
- Decimal precision throughout (no float drift in the swap-payback math).
- Zero extra dependencies, easy to audit, easy to test exactly.

All public functions are pure; tests pass synthetic bar series and check
the result against hand-computed expected values.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable

from trading_agent_skills.decimal_io import D


class InsufficientBars(ValueError):
    """Raised when a series is too short for the requested period."""


@dataclass(frozen=True)
class Bar:
    time_utc: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int = 0

    @classmethod
    def from_mcp(cls, blob: dict[str, Any]) -> "Bar":
        time_raw = blob.get("time") or blob.get("time_utc")
        if time_raw is None:
            raise KeyError("bar missing 'time'")
        time_dt = (
            time_raw
            if isinstance(time_raw, datetime)
            else datetime.fromisoformat(str(time_raw).replace("Z", "+00:00"))
        )
        return cls(
            time_utc=time_dt,
            open=D(blob["open"]),
            high=D(blob["high"]),
            low=D(blob["low"]),
            close=D(blob["close"]),
            volume=int(blob.get("volume", 0) or 0),
        )


def _validate_period(period: int) -> None:
    if period <= 0:
        raise ValueError(f"period must be > 0, got {period}")


# ---------- ATR (Wilder) ---------------------------------------------------


def true_ranges(bars: list[Bar]) -> list[Decimal]:
    """Per-bar true range. First bar's TR is high − low (no prior close)."""
    if not bars:
        return []
    out: list[Decimal] = [bars[0].high - bars[0].low]
    for prev, cur in zip(bars, bars[1:]):
        tr = max(
            cur.high - cur.low,
            abs(cur.high - prev.close),
            abs(cur.low - prev.close),
        )
        out.append(tr)
    return out


def atr(bars: list[Bar], period: int = 14) -> Decimal:
    """Wilder ATR on a flat list of bars (oldest first)."""
    _validate_period(period)
    trs = true_ranges(bars)
    # Need ``period`` TRs after dropping the first synthetic TR? Actually we
    # bootstrap from the first ``period`` TRs (including bar[0]). Require
    # ``period`` bars minimum.
    if len(trs) < period:
        raise InsufficientBars(
            f"ATR({period}) needs {period} bars, got {len(bars)}"
        )
    initial = sum(trs[:period], start=Decimal("0")) / Decimal(period)
    smoothed = initial
    for tr in trs[period:]:
        smoothed = (smoothed * Decimal(period - 1) + tr) / Decimal(period)
    return smoothed


# ---------- RSI (Wilder) ---------------------------------------------------


def rsi(bars: list[Bar], period: int = 14) -> Decimal:
    """Wilder RSI on closes (oldest first). 0..100 Decimal.

    Returns 100 when there's been no down-move in the smoothing window.
    """
    _validate_period(period)
    if len(bars) < period + 1:
        raise InsufficientBars(
            f"RSI({period}) needs {period + 1} bars, got {len(bars)}"
        )
    gains: list[Decimal] = []
    losses: list[Decimal] = []
    for prev, cur in zip(bars, bars[1:]):
        delta = cur.close - prev.close
        gains.append(delta if delta > 0 else Decimal("0"))
        losses.append(-delta if delta < 0 else Decimal("0"))

    avg_gain = sum(gains[:period], start=Decimal("0")) / Decimal(period)
    avg_loss = sum(losses[:period], start=Decimal("0")) / Decimal(period)

    for g, l in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * Decimal(period - 1) + g) / Decimal(period)
        avg_loss = (avg_loss * Decimal(period - 1) + l) / Decimal(period)

    if avg_loss == 0:
        return Decimal("100") if avg_gain > 0 else Decimal("50")
    rs = avg_gain / avg_loss
    return Decimal("100") - Decimal("100") / (Decimal("1") + rs)


# ---------- EMA ------------------------------------------------------------


def ema(bars: list[Bar], period: int = 20) -> Decimal:
    """Standard EMA over closes, seeded with SMA of the first ``period`` closes."""
    _validate_period(period)
    if len(bars) < period:
        raise InsufficientBars(
            f"EMA({period}) needs {period} bars, got {len(bars)}"
        )
    closes = [b.close for b in bars]
    seed = sum(closes[:period], start=Decimal("0")) / Decimal(period)
    if len(bars) == period:
        return seed
    alpha = Decimal("2") / Decimal(period + 1)
    one_minus_alpha = Decimal("1") - alpha
    smoothed = seed
    for c in closes[period:]:
        smoothed = alpha * c + one_minus_alpha * smoothed
    return smoothed


# ---------- Helpers ---------------------------------------------------------


@dataclass(frozen=True)
class IndicatorSnapshot:
    symbol: str
    rsi_14: Decimal
    atr_14: Decimal
    atr_pct_of_price: Decimal
    ema_20: Decimal
    distance_from_ema_atr_units: Decimal
    last_close: Decimal


def snapshot(symbol: str, bars: list[Bar]) -> IndicatorSnapshot:
    """Compute a per-symbol indicator snapshot used by news_brief.

    Raises ``InsufficientBars`` if any single indicator can't be computed.
    Caller decides whether to skip the symbol or surface a flag.
    """
    if not bars:
        raise InsufficientBars(f"{symbol}: empty bar series")
    last = bars[-1].close
    atr_v = atr(bars, 14)
    rsi_v = rsi(bars, 14)
    ema_v = ema(bars, 20)
    atr_pct = (atr_v / last * Decimal("100")) if last > 0 else Decimal("0")
    dist_atr_units = (
        (last - ema_v) / atr_v if atr_v > 0 else Decimal("0")
    )
    return IndicatorSnapshot(
        symbol=symbol,
        rsi_14=rsi_v,
        atr_14=atr_v,
        atr_pct_of_price=atr_pct,
        ema_20=ema_v,
        distance_from_ema_atr_units=dist_atr_units,
        last_close=last,
    )


def bars_from_mcp(entries: Iterable[dict[str, Any]]) -> list[Bar]:
    """Convenience: build a Bar list from a list of mt5-mcp ``get_rates`` entries."""
    return [Bar.from_mcp(e) for e in entries]


__all__ = [
    "InsufficientBars",
    "Bar",
    "true_ranges",
    "atr",
    "rsi",
    "ema",
    "IndicatorSnapshot",
    "snapshot",
    "bars_from_mcp",
]
