"""Detector: rejection candle at a clustered S/R level."""

from __future__ import annotations

from decimal import Decimal

from trading_agent_skills.price_action.context import ScanContext
from trading_agent_skills.price_action.detectors import CandidateSetup, EntryZone


_PROXIMITY_TICKS = 50


def detect(ctx: ScanContext) -> list[CandidateSetup]:
    tfs = list(ctx.tfs.keys())
    if not tfs:
        return []
    setup_tf = tfs[-1]
    trig_tf = tfs[0]
    sctx = ctx.tfs[setup_tf]
    if not sctx.bars or not sctx.sr_levels:
        return []
    last = sctx.bars[-1]
    proximity = ctx.tick_size * Decimal(_PROXIMITY_TICKS)
    out: list[CandidateSetup] = []
    for lvl in sctx.sr_levels:
        if lvl.side == "support":
            if last.low <= lvl.price + proximity and last.close > lvl.price:
                out.append(CandidateSetup(
                    type="sr_bounce",
                    tf_setup=setup_tf,
                    tf_trigger=trig_tf,
                    side="long",
                    entry_zone=EntryZone(low=lvl.price, high=last.close),
                    suggested_entry=lvl.price + proximity / Decimal(2),
                    invalidation=lvl.price - proximity,
                    targets=(),
                    confluence=(
                        f"{setup_tf}_support_x{lvl.tested}",
                    ),
                    candle_quality=Decimal("0.55"),
                    narrative_hint=(
                        f"{setup_tf} support {lvl.price} tested {lvl.tested}× "
                        "with last-bar rejection"
                    ),
                ))
        else:
            if last.high >= lvl.price - proximity and last.close < lvl.price:
                out.append(CandidateSetup(
                    type="sr_bounce",
                    tf_setup=setup_tf,
                    tf_trigger=trig_tf,
                    side="short",
                    entry_zone=EntryZone(low=last.close, high=lvl.price),
                    suggested_entry=lvl.price - proximity / Decimal(2),
                    invalidation=lvl.price + proximity,
                    targets=(),
                    confluence=(
                        f"{setup_tf}_resistance_x{lvl.tested}",
                    ),
                    candle_quality=Decimal("0.55"),
                    narrative_hint=(
                        f"{setup_tf} resistance {lvl.price} tested {lvl.tested}× "
                        "with last-bar rejection"
                    ),
                ))
    return out


__all__ = ["detect"]
