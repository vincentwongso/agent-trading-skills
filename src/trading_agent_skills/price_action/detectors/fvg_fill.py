"""Detector: price returning to fill an unfilled FVG."""

from __future__ import annotations

from decimal import Decimal

from trading_agent_skills.price_action.context import ScanContext
from trading_agent_skills.price_action.detectors import CandidateSetup, EntryZone


def detect(ctx: ScanContext) -> list[CandidateSetup]:
    tfs = list(ctx.tfs.keys())
    if not tfs:
        return []
    setup_tf = tfs[-1]
    trig_tf = tfs[0]
    sctx = ctx.tfs[setup_tf]
    if not sctx.fvgs or not sctx.bars:
        return []
    last = sctx.bars[-1]
    out: list[CandidateSetup] = []
    for fvg in sctx.fvgs:
        if fvg.filled_pct >= Decimal("0.5"):
            continue
        if fvg.side == "demand":
            if last.low <= fvg.high and last.low >= fvg.low - ctx.tick_size * Decimal("10"):
                mid = (fvg.low + fvg.high) / Decimal(2)
                out.append(CandidateSetup(
                    type="fvg_fill",
                    tf_setup=setup_tf, tf_trigger=trig_tf,
                    side="long",
                    entry_zone=EntryZone(low=fvg.low, high=fvg.high),
                    suggested_entry=mid,
                    invalidation=fvg.low - ctx.tick_size * Decimal("20"),
                    targets=(),
                    confluence=(f"{setup_tf}_demand_FVG",),
                    candle_quality=Decimal("0.65"),
                    narrative_hint=(
                        f"{setup_tf} demand FVG {fvg.low}..{fvg.high} "
                        f"(filled {fvg.filled_pct})"
                    ),
                ))
        else:
            if last.high >= fvg.low and last.high <= fvg.high + ctx.tick_size * Decimal("10"):
                mid = (fvg.low + fvg.high) / Decimal(2)
                out.append(CandidateSetup(
                    type="fvg_fill",
                    tf_setup=setup_tf, tf_trigger=trig_tf,
                    side="short",
                    entry_zone=EntryZone(low=fvg.low, high=fvg.high),
                    suggested_entry=mid,
                    invalidation=fvg.high + ctx.tick_size * Decimal("20"),
                    targets=(),
                    confluence=(f"{setup_tf}_supply_FVG",),
                    candle_quality=Decimal("0.65"),
                    narrative_hint=(
                        f"{setup_tf} supply FVG {fvg.low}..{fvg.high} "
                        f"(filled {fvg.filled_pct})"
                    ),
                ))
    return out


__all__ = ["detect"]
