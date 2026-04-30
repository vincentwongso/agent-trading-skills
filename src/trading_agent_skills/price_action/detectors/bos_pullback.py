"""Detector: break of structure (BOS) on setup TF + pullback on trigger TF."""

from __future__ import annotations

from decimal import Decimal

from trading_agent_skills.price_action.context import ScanContext
from trading_agent_skills.price_action.detectors import CandidateSetup, EntryZone


_PROXIMITY_TICKS = 30


def detect(ctx: ScanContext) -> list[CandidateSetup]:
    tfs = list(ctx.tfs.keys())
    if not tfs:
        return []
    setup_tf = tfs[-1]
    trig_tf = tfs[0]
    sctx = ctx.tfs[setup_tf]
    if not sctx.bars or not sctx.pivots:
        return []
    last = sctx.bars[-1]
    proximity = ctx.tick_size * Decimal(_PROXIMITY_TICKS)
    out: list[CandidateSetup] = []
    highs = [p for p in sctx.pivots if p.kind == "swing_high"]
    lows = [p for p in sctx.pivots if p.kind == "swing_low"]
    if sctx.regime == "trend_up" and len(highs) >= 2 and highs[-1].label == "HH":
        broken_edge = highs[-2].price
        if last.low <= broken_edge + proximity and last.close > broken_edge:
            out.append(CandidateSetup(
                type="bos_pullback",
                tf_setup=setup_tf, tf_trigger=trig_tf,
                side="long",
                entry_zone=EntryZone(low=broken_edge, high=last.close),
                suggested_entry=broken_edge + proximity / Decimal(2),
                invalidation=broken_edge - proximity,
                targets=(),
                confluence=(f"{setup_tf}_BOS_up", "broken_edge_retest"),
                candle_quality=Decimal("0.7"),
                narrative_hint=(
                    f"{setup_tf} BOS above {broken_edge}; pullback retest"
                ),
            ))
    if sctx.regime == "trend_down" and len(lows) >= 2 and lows[-1].label == "LL":
        broken_edge = lows[-2].price
        if last.high >= broken_edge - proximity and last.close < broken_edge:
            out.append(CandidateSetup(
                type="bos_pullback",
                tf_setup=setup_tf, tf_trigger=trig_tf,
                side="short",
                entry_zone=EntryZone(low=last.close, high=broken_edge),
                suggested_entry=broken_edge - proximity / Decimal(2),
                invalidation=broken_edge + proximity,
                targets=(),
                confluence=(f"{setup_tf}_BOS_down", "broken_edge_retest"),
                candle_quality=Decimal("0.7"),
                narrative_hint=(
                    f"{setup_tf} BOS below {broken_edge}; pullback retest"
                ),
            ))
    return out


__all__ = ["detect"]
