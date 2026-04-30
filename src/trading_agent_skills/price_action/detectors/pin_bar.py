"""Detector: pin bar (long-wick rejection candle) at S/R or FVG."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal, Optional

from trading_agent_skills.indicators import Bar
from trading_agent_skills.price_action.context import ScanContext
from trading_agent_skills.price_action.detectors import CandidateSetup, EntryZone


PinSide = Literal["bullish", "bearish"]
_PROXIMITY_TICKS = 50


def is_pin_bar(
    bar: Bar,
    *,
    min_wick_to_body: Decimal,
) -> tuple[Optional[PinSide], Decimal]:
    body = abs(bar.close - bar.open)
    if body == 0:
        body = Decimal("0.0001")
    upper_wick = bar.high - max(bar.open, bar.close)
    lower_wick = min(bar.open, bar.close) - bar.low
    bull_ratio = lower_wick / body if body > 0 else Decimal("0")
    bear_ratio = upper_wick / body if body > 0 else Decimal("0")
    if bull_ratio >= min_wick_to_body and lower_wick > upper_wick * Decimal("2"):
        return "bullish", bull_ratio
    if bear_ratio >= min_wick_to_body and upper_wick > lower_wick * Decimal("2"):
        return "bearish", bear_ratio
    return None, Decimal("0")


def detect(ctx: ScanContext) -> list[CandidateSetup]:
    tfs = list(ctx.tfs.keys())
    if not tfs:
        return []
    setup_tf = tfs[-1]
    trig_tf = tfs[0]
    sctx = ctx.tfs[setup_tf]
    if not sctx.bars:
        return []
    last = sctx.bars[-1]
    side, ratio = is_pin_bar(last, min_wick_to_body=Decimal("2"))
    if side is None:
        return []
    proximity = ctx.tick_size * Decimal(_PROXIMITY_TICKS)
    out: list[CandidateSetup] = []
    quality = min(ratio / Decimal("4"), Decimal("1"))
    for lvl in sctx.sr_levels:
        near = abs(last.low - lvl.price) <= proximity if side == "bullish" \
            else abs(last.high - lvl.price) <= proximity
        if not near:
            continue
        if side == "bullish" and lvl.side == "support":
            out.append(CandidateSetup(
                type="pin_bar",
                tf_setup=setup_tf, tf_trigger=trig_tf,
                side="long",
                entry_zone=EntryZone(low=last.low, high=last.close),
                suggested_entry=(last.open + last.close) / Decimal(2),
                invalidation=last.low - proximity,
                targets=(),
                confluence=(f"{setup_tf}_support", "pin_bar_bullish"),
                candle_quality=quality,
                narrative_hint=(
                    f"Bullish pin bar at {setup_tf} support {lvl.price}"
                ),
            ))
        elif side == "bearish" and lvl.side == "resistance":
            out.append(CandidateSetup(
                type="pin_bar",
                tf_setup=setup_tf, tf_trigger=trig_tf,
                side="short",
                entry_zone=EntryZone(low=last.close, high=last.high),
                suggested_entry=(last.open + last.close) / Decimal(2),
                invalidation=last.high + proximity,
                targets=(),
                confluence=(f"{setup_tf}_resistance", "pin_bar_bearish"),
                candle_quality=quality,
                narrative_hint=(
                    f"Bearish pin bar at {setup_tf} resistance {lvl.price}"
                ),
            ))
    return out


__all__ = ["detect", "is_pin_bar", "PinSide"]
