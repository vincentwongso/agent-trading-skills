"""Detector: liquidity sweep (BSL/SSL grab) followed by reversal candle."""

from __future__ import annotations

from decimal import Decimal

from cfd_skills.price_action.context import ScanContext
from cfd_skills.price_action.detectors import CandidateSetup, EntryZone


def detect(ctx: ScanContext) -> list[CandidateSetup]:
    tfs = list(ctx.tfs.keys())
    if not tfs:
        return []
    setup_tf = tfs[-1]
    trig_tf = tfs[0]
    sctx = ctx.tfs[setup_tf]
    if not sctx.liquidity_pools or not sctx.bars:
        return []
    last = sctx.bars[-1]
    out: list[CandidateSetup] = []
    for pool in sctx.liquidity_pools:
        if pool.type == "SSL" and last.low < pool.price and last.close > pool.price:
            out.append(CandidateSetup(
                type="liq_sweep",
                tf_setup=setup_tf, tf_trigger=trig_tf,
                side="long",
                entry_zone=EntryZone(low=last.low, high=pool.price),
                suggested_entry=pool.price,
                invalidation=last.low - ctx.tick_size * Decimal("10"),
                targets=(),
                confluence=(f"{setup_tf}_SSL_swept@{pool.price}",),
                candle_quality=Decimal("0.75"),
                narrative_hint=(
                    f"{setup_tf} SSL pool {pool.price} swept; close back above"
                ),
            ))
        elif pool.type == "BSL" and last.high > pool.price and last.close < pool.price:
            out.append(CandidateSetup(
                type="liq_sweep",
                tf_setup=setup_tf, tf_trigger=trig_tf,
                side="short",
                entry_zone=EntryZone(low=pool.price, high=last.high),
                suggested_entry=pool.price,
                invalidation=last.high + ctx.tick_size * Decimal("10"),
                targets=(),
                confluence=(f"{setup_tf}_BSL_swept@{pool.price}",),
                candle_quality=Decimal("0.75"),
                narrative_hint=(
                    f"{setup_tf} BSL pool {pool.price} swept; close back below"
                ),
            ))
    return out


__all__ = ["detect"]
