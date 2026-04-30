"""Fractal pivot detection on Decimal OHLC bars.

A fractal pivot high at index i requires bars[i].high to be strictly
greater than bars[j].high for all j in [i-N, i+N] \\ {i}, where N is the
lookback parameter (default 3 — Bill Williams classic). Pivot lows are
defined symmetrically on bars[i].low.

Pivots are classified post-detection into HH / HL / LH / LL relative to
the prior same-kind pivot, surfacing trend structure for the regime
classifier in ``structure.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Literal

from trading_agent_skills.indicators import Bar


PivotKind = Literal["swing_high", "swing_low"]
PivotLabel = Literal["HH", "HL", "LH", "LL"]


@dataclass(frozen=True)
class Pivot:
    index: int
    time_utc: datetime
    price: Decimal
    kind: PivotKind
    label: PivotLabel | None = None


def detect_pivots(bars: list[Bar], *, lookback: int = 3) -> list[Pivot]:
    """Return fractal pivots ordered by bar index (oldest first).

    ``lookback`` bars are required on either side; bars too close to
    either edge are not confirmed. Equal highs/lows do not form a pivot
    (strict inequality).
    """
    if lookback <= 0:
        raise ValueError(f"lookback must be > 0, got {lookback}")
    out: list[Pivot] = []
    for i in range(lookback, len(bars) - lookback):
        cur = bars[i]
        window = bars[i - lookback : i + lookback + 1]
        if all(cur.high > b.high for b in window if b is not cur):
            out.append(Pivot(i, cur.time_utc, cur.high, "swing_high"))
        elif all(cur.low < b.low for b in window if b is not cur):
            out.append(Pivot(i, cur.time_utc, cur.low, "swing_low"))
    return out


def classify_sequence(pivots: list[Pivot]) -> list[Pivot]:
    """Tag each pivot with HH/HL/LH/LL relative to the prior same-kind pivot.

    The first pivot of each kind has ``label=None`` (no comparison).
    """
    last_high: Decimal | None = None
    last_low: Decimal | None = None
    out: list[Pivot] = []
    for p in pivots:
        label: PivotLabel | None = None
        if p.kind == "swing_high":
            if last_high is not None:
                label = "HH" if p.price > last_high else "LH"
            last_high = p.price
        else:  # swing_low
            if last_low is not None:
                label = "HL" if p.price > last_low else "LL"
            last_low = p.price
        out.append(replace(p, label=label))
    return out


__all__ = ["Pivot", "PivotKind", "PivotLabel", "detect_pivots", "classify_sequence"]
