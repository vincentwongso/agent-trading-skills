"""Order Block detection — last opposing candle before strong displacement.

A demand OB is the last red (bear) candle before an upward impulse whose
net move (close of the displacement window minus open of the OB candle)
is at least ``displacement_atr_mult * ATR``. A supply OB is the
symmetric construct.

"Last opposing" is enforced by requiring the candle immediately following
the OB to point the same direction as the impulse (green for demand, red
for supply). This prevents emitting an OB for every red candle in a
multi-bar pullback.

OBs track a retest flag: True once a later bar (after the displacement
window) wicks back into the OB range from the displaced side.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from cfd_skills.indicators import Bar


OBSide = Literal["demand", "supply"]


@dataclass(frozen=True)
class OrderBlock:
    high: Decimal
    low: Decimal
    side: OBSide
    tf: str
    created_index: int
    created_time_utc: datetime
    retested: bool


def _is_red(b: Bar) -> bool:
    return b.close < b.open


def _is_green(b: Bar) -> bool:
    return b.close > b.open


def detect_order_blocks(
    bars: list[Bar],
    *,
    tf: str,
    atr: Decimal,
    displacement_atr_mult: Decimal,
    displacement_lookahead: int = 3,
) -> list[OrderBlock]:
    """Scan for OBs validated by displacement within the next K bars."""
    out: list[OrderBlock] = []
    if len(bars) < 2 or atr <= 0:
        return out
    threshold = atr * displacement_atr_mult
    for i in range(len(bars) - 1):
        cur = bars[i]
        nxt = bars[i + 1]
        window = bars[i + 1 : i + 1 + displacement_lookahead]
        if not window:
            continue
        net_up = window[-1].close - cur.open
        net_down = cur.open - window[-1].close
        if _is_red(cur) and _is_green(nxt) and net_up >= threshold:
            ob_low, ob_high = cur.low, cur.high
            retested = any(
                later.low <= ob_high
                for later in bars[i + 1 + displacement_lookahead :]
            )
            out.append(OrderBlock(
                high=ob_high, low=ob_low, side="demand", tf=tf,
                created_index=i, created_time_utc=cur.time_utc,
                retested=retested,
            ))
        elif _is_green(cur) and _is_red(nxt) and net_down >= threshold:
            ob_low, ob_high = cur.low, cur.high
            retested = any(
                later.high >= ob_low
                for later in bars[i + 1 + displacement_lookahead :]
            )
            out.append(OrderBlock(
                high=ob_high, low=ob_low, side="supply", tf=tf,
                created_index=i, created_time_utc=cur.time_utc,
                retested=retested,
            ))
    return out


__all__ = ["OrderBlock", "OBSide", "detect_order_blocks"]
