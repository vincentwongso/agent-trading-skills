"""Fair Value Gap (FVG) detection on three-bar windows.

A bullish (demand) FVG forms when bar[i+1] makes a strong impulse up such
that bar[i].high < bar[i+2].low. The gap region is [bar[i].high,
bar[i+2].low] — price has "skipped" this band on the way up.

Symmetric for bearish (supply) FVGs: bar[i].low > bar[i+2].high → gap
[bar[i+2].high, bar[i].low].

Fill state is tracked by examining all bars after the FVG's third bar.
``filled_pct`` is the largest fill seen, in [0, 1]. Fully filled FVGs
(filled_pct >= 1) are excluded from the default return — callers can
opt in via ``include_filled=True`` if they need historical FVGs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from cfd_skills.indicators import Bar


FVGSide = Literal["demand", "supply"]


@dataclass(frozen=True)
class FVG:
    high: Decimal
    low: Decimal
    side: FVGSide
    tf: str
    created_index: int
    created_time_utc: datetime
    filled_pct: Decimal  # [0, 1]


def detect_fvgs(
    bars: list[Bar],
    *,
    tf: str,
    include_filled: bool = False,
) -> list[FVG]:
    """Scan three-bar windows for FVGs and compute fill state from later bars."""
    if len(bars) < 3:
        return []
    out: list[FVG] = []
    for i in range(len(bars) - 2):
        b1, b2, b3 = bars[i], bars[i + 1], bars[i + 2]
        if b1.high < b3.low:
            fvg_low, fvg_high = b1.high, b3.low
            side: FVGSide = "demand"
        elif b1.low > b3.high:
            fvg_low, fvg_high = b3.high, b1.low
            side = "supply"
        else:
            continue
        span = fvg_high - fvg_low
        if span <= 0:
            continue
        max_fill = Decimal("0")
        for later in bars[i + 3 :]:
            if side == "demand":
                penetration = max(Decimal("0"), fvg_high - max(later.low, fvg_low))
            else:
                penetration = max(Decimal("0"), min(later.high, fvg_high) - fvg_low)
            pct = penetration / span if span > 0 else Decimal("0")
            if pct > max_fill:
                max_fill = pct
            if max_fill >= Decimal("1"):
                max_fill = Decimal("1")
                break
        if not include_filled and max_fill >= Decimal("1"):
            continue
        out.append(FVG(
            high=fvg_high, low=fvg_low, side=side, tf=tf,
            created_index=i + 2, created_time_utc=b3.time_utc,
            filled_pct=max_fill,
        ))
    return out


__all__ = ["FVG", "FVGSide", "detect_fvgs"]
