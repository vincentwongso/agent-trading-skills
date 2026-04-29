"""Detector: engulfing candle at S/R or FVG."""

from __future__ import annotations

from decimal import Decimal

from cfd_skills.indicators import Bar
from cfd_skills.price_action.context import ScanContext
from cfd_skills.price_action.detectors import CandidateSetup, EntryZone


_PROXIMITY_TICKS = 50


def is_bullish_engulfing(prev: Bar, cur: Bar) -> bool:
    return (
        prev.close < prev.open
        and cur.close > cur.open
        and cur.open <= prev.close
        and cur.close >= prev.open
    )


def is_bearish_engulfing(prev: Bar, cur: Bar) -> bool:
    return (
        prev.close > prev.open
        and cur.close < cur.open
        and cur.open >= prev.close
        and cur.close <= prev.open
    )


def detect(ctx: ScanContext) -> list[CandidateSetup]:
    tfs = list(ctx.tfs.keys())
    if not tfs:
        return []
    setup_tf = tfs[-1]
    trig_tf = tfs[0]
    sctx = ctx.tfs[setup_tf]
    if len(sctx.bars) < 2:
        return []
    prev, cur = sctx.bars[-2], sctx.bars[-1]
    proximity = ctx.tick_size * Decimal(_PROXIMITY_TICKS)
    out: list[CandidateSetup] = []
    if is_bullish_engulfing(prev, cur):
        for lvl in sctx.sr_levels:
            if lvl.side == "support" and abs(cur.low - lvl.price) <= proximity:
                out.append(CandidateSetup(
                    type="engulfing",
                    tf_setup=setup_tf, tf_trigger=trig_tf,
                    side="long",
                    entry_zone=EntryZone(low=cur.open, high=cur.close),
                    suggested_entry=cur.close,
                    invalidation=cur.low - proximity,
                    targets=(),
                    confluence=(f"{setup_tf}_support", "bull_engulfing"),
                    candle_quality=Decimal("0.7"),
                    narrative_hint=f"Bullish engulfing at {setup_tf} support {lvl.price}",
                ))
                break
    if is_bearish_engulfing(prev, cur):
        for lvl in sctx.sr_levels:
            if lvl.side == "resistance" and abs(cur.high - lvl.price) <= proximity:
                out.append(CandidateSetup(
                    type="engulfing",
                    tf_setup=setup_tf, tf_trigger=trig_tf,
                    side="short",
                    entry_zone=EntryZone(low=cur.close, high=cur.open),
                    suggested_entry=cur.close,
                    invalidation=cur.high + proximity,
                    targets=(),
                    confluence=(f"{setup_tf}_resistance", "bear_engulfing"),
                    candle_quality=Decimal("0.7"),
                    narrative_hint=f"Bearish engulfing at {setup_tf} resistance {lvl.price}",
                ))
                break
    return out


__all__ = ["detect", "is_bullish_engulfing", "is_bearish_engulfing"]
