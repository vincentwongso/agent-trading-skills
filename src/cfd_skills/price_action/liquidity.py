"""Liquidity pool derivation: BSL above swing highs, SSL below swing lows.

A pool is "swept" when subsequent price action has traded through the
pool's price (high above a BSL level, low below an SSL level). The
"sweep + reversal" detector consumes these pools.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from cfd_skills.price_action.pivots import Pivot


PoolType = Literal["BSL", "SSL"]


@dataclass(frozen=True)
class LiquidityPool:
    price: Decimal
    type: PoolType
    tf: str
    created_index: int
    created_time_utc: datetime
    swept: bool


def derive_liquidity_pools(
    pivots: list[Pivot],
    *,
    tf: str,
    max_subsequent_high: Decimal,
    max_subsequent_low: Decimal,
) -> list[LiquidityPool]:
    """Convert pivots into liquidity pools, tagging sweep state."""
    out: list[LiquidityPool] = []
    for p in pivots:
        if p.kind == "swing_high":
            out.append(LiquidityPool(
                price=p.price, type="BSL", tf=tf,
                created_index=p.index, created_time_utc=p.time_utc,
                swept=max_subsequent_high > p.price,
            ))
        else:
            out.append(LiquidityPool(
                price=p.price, type="SSL", tf=tf,
                created_index=p.index, created_time_utc=p.time_utc,
                swept=max_subsequent_low < p.price,
            ))
    return out


__all__ = ["LiquidityPool", "PoolType", "derive_liquidity_pools"]
