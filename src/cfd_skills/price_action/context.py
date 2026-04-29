"""Composes per-TF derived structure into a single ScanContext.

Each detector consumes a ``ScanContext`` rather than re-deriving pivots /
S/R / FVGs / OBs / liquidity / EMA stack from raw bars. This keeps the
detectors pure and trivially unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from cfd_skills.indicators import Bar, InsufficientBars, atr, ema
from cfd_skills.price_action.bars import MTFBars
from cfd_skills.price_action.fvg import FVG, detect_fvgs
from cfd_skills.price_action.liquidity import LiquidityPool, derive_liquidity_pools
from cfd_skills.price_action.order_block import OrderBlock, detect_order_blocks
from cfd_skills.price_action.pivots import Pivot, classify_sequence, detect_pivots
from cfd_skills.price_action.structure import (
    MTFAlignment,
    RegimeKind,
    SRLevel,
    classify_mtf_alignment,
    classify_regime,
    cluster_sr_levels,
)


@dataclass(frozen=True)
class TFContext:
    tf: str
    bars: list[Bar]
    pivots: list[Pivot]
    sr_levels: list[SRLevel]
    fvgs: list[FVG]
    order_blocks: list[OrderBlock]
    liquidity_pools: list[LiquidityPool]
    regime: RegimeKind
    ema21: Optional[Decimal]
    ema50: Optional[Decimal]
    atr14: Optional[Decimal]


@dataclass(frozen=True)
class ScanContext:
    symbol: str
    current_price: Decimal
    tick_size: Decimal
    digits: int
    tfs: dict[str, TFContext]
    mtf_alignment: MTFAlignment


def _safe_ema(bars: list[Bar], period: int) -> Optional[Decimal]:
    try:
        return ema(bars, period)
    except InsufficientBars:
        return None


def _safe_atr(bars: list[Bar], period: int = 14) -> Optional[Decimal]:
    try:
        return atr(bars, period)
    except InsufficientBars:
        return None


def build_context(
    *,
    symbol: str,
    mtf: MTFBars,
    current_price: Decimal,
    tick_size: Decimal,
    digits: int,
    cluster_factor: int,
    pivot_lookback: int,
    displacement_atr_mult: Decimal,
) -> ScanContext:
    """Derive per-TF structure + overall MTF alignment."""
    tfs: dict[str, TFContext] = {}
    regime_by_tf: dict[str, RegimeKind] = {}

    for tf in mtf.timeframes():
        bars = mtf.bars(tf)
        raw_pivots = detect_pivots(bars, lookback=pivot_lookback)
        pivots = classify_sequence(raw_pivots)
        sr = cluster_sr_levels(
            pivots, tick_size=tick_size, cluster_factor=cluster_factor, tf=tf,
        )
        fvgs = detect_fvgs(bars, tf=tf)
        atr14 = _safe_atr(bars, 14)
        if atr14 is not None and atr14 > 0:
            obs = detect_order_blocks(
                bars, tf=tf,
                atr=atr14,
                displacement_atr_mult=displacement_atr_mult,
            )
        else:
            obs = []
        if bars:
            running_high = max((b.high for b in bars), default=Decimal("0"))
            running_low = min((b.low for b in bars), default=current_price)
        else:
            running_high = current_price
            running_low = current_price
        pools = derive_liquidity_pools(
            pivots, tf=tf,
            max_subsequent_high=running_high,
            max_subsequent_low=running_low,
        )
        regime = classify_regime(pivots)
        regime_by_tf[tf] = regime
        tfs[tf] = TFContext(
            tf=tf, bars=bars, pivots=pivots, sr_levels=sr, fvgs=fvgs,
            order_blocks=obs, liquidity_pools=pools, regime=regime,
            ema21=_safe_ema(bars, 21),
            ema50=_safe_ema(bars, 50),
            atr14=atr14,
        )

    return ScanContext(
        symbol=symbol,
        current_price=current_price,
        tick_size=tick_size,
        digits=digits,
        tfs=tfs,
        mtf_alignment=classify_mtf_alignment(regime_by_tf),
    )


__all__ = ["TFContext", "ScanContext", "build_context"]
