"""Detector: N-bar range break + retest of broken edge."""

from __future__ import annotations

from decimal import Decimal

from cfd_skills.price_action.context import ScanContext
from cfd_skills.price_action.detectors import CandidateSetup, EntryZone


_RANGE_WINDOW = 30
_BREAK_WINDOW = 5
_RETEST_PROXIMITY_TICKS = 30


def detect(ctx: ScanContext) -> list[CandidateSetup]:
    tfs = list(ctx.tfs.keys())
    if not tfs:
        return []
    setup_tf = tfs[-1]
    trig_tf = tfs[0]
    sctx = ctx.tfs[setup_tf]
    bars = sctx.bars
    if len(bars) < _RANGE_WINDOW + _BREAK_WINDOW:
        return []
    range_bars = bars[-(_RANGE_WINDOW + _BREAK_WINDOW) : -_BREAK_WINDOW]
    break_bars = bars[-_BREAK_WINDOW:]
    range_high = max(b.high for b in range_bars)
    range_low = min(b.low for b in range_bars)
    proximity = ctx.tick_size * Decimal(_RETEST_PROXIMITY_TICKS)
    last = bars[-1]
    out: list[CandidateSetup] = []
    if any(b.high > range_high for b in break_bars[:-1]):
        if last.low <= range_high + proximity and last.close > range_high:
            out.append(CandidateSetup(
                type="range_break_retest",
                tf_setup=setup_tf, tf_trigger=trig_tf,
                side="long",
                entry_zone=EntryZone(low=range_high, high=last.close),
                suggested_entry=range_high + proximity / Decimal(2),
                invalidation=range_high - proximity,
                targets=(),
                confluence=(f"{setup_tf}_range_break_up",),
                candle_quality=Decimal("0.6"),
                narrative_hint=(
                    f"{setup_tf} broke {_RANGE_WINDOW}-bar range high "
                    f"{range_high}; retest from above"
                ),
            ))
    if any(b.low < range_low for b in break_bars[:-1]):
        if last.high >= range_low - proximity and last.close < range_low:
            out.append(CandidateSetup(
                type="range_break_retest",
                tf_setup=setup_tf, tf_trigger=trig_tf,
                side="short",
                entry_zone=EntryZone(low=last.close, high=range_low),
                suggested_entry=range_low - proximity / Decimal(2),
                invalidation=range_low + proximity,
                targets=(),
                confluence=(f"{setup_tf}_range_break_down",),
                candle_quality=Decimal("0.6"),
                narrative_hint=(
                    f"{setup_tf} broke {_RANGE_WINDOW}-bar range low "
                    f"{range_low}; retest from below"
                ),
            ))
    return out


__all__ = ["detect"]
