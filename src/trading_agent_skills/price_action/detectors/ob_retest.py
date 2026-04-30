"""Detector: retest of an order block from the displaced side."""

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
    if not sctx.order_blocks or not sctx.bars:
        return []
    last = sctx.bars[-1]
    out: list[CandidateSetup] = []
    for ob in sctx.order_blocks:
        if not ob.retested:
            continue
        if ob.side == "demand" and last.low <= ob.high:
            mid = (ob.low + ob.high) / Decimal(2)
            out.append(CandidateSetup(
                type="ob_retest",
                tf_setup=setup_tf, tf_trigger=trig_tf,
                side="long",
                entry_zone=EntryZone(low=ob.low, high=ob.high),
                suggested_entry=mid,
                invalidation=ob.low - ctx.tick_size * Decimal("20"),
                targets=(),
                confluence=(f"{setup_tf}_demand_OB",),
                candle_quality=Decimal("0.7"),
                narrative_hint=f"{setup_tf} demand OB retest {ob.low}..{ob.high}",
            ))
        elif ob.side == "supply" and last.high >= ob.low:
            mid = (ob.low + ob.high) / Decimal(2)
            out.append(CandidateSetup(
                type="ob_retest",
                tf_setup=setup_tf, tf_trigger=trig_tf,
                side="short",
                entry_zone=EntryZone(low=ob.low, high=ob.high),
                suggested_entry=mid,
                invalidation=ob.high + ctx.tick_size * Decimal("20"),
                targets=(),
                confluence=(f"{setup_tf}_supply_OB",),
                candle_quality=Decimal("0.7"),
                narrative_hint=f"{setup_tf} supply OB retest {ob.low}..{ob.high}",
            ))
    return out


__all__ = ["detect"]
