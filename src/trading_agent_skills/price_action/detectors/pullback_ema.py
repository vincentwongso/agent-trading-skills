"""Detector: pullback to EMA21 in EMA-stack-aligned trend."""

from __future__ import annotations

from decimal import Decimal

from trading_agent_skills.price_action.context import ScanContext
from trading_agent_skills.price_action.detectors import (
    CandidateSetup,
    EntryZone,
)


def _pick_setup_tf(ctx: ScanContext) -> str | None:
    tfs = list(ctx.tfs.keys())
    if not tfs:
        return None
    if len(tfs) >= 2:
        return tfs[-2]
    return tfs[-1]


def _pick_trigger_tf(ctx: ScanContext) -> str:
    tfs = list(ctx.tfs.keys())
    return tfs[0] if tfs else ""


def detect(ctx: ScanContext) -> list[CandidateSetup]:
    setup_tf = _pick_setup_tf(ctx)
    trig_tf = _pick_trigger_tf(ctx)
    if setup_tf is None:
        return []
    tfctx = ctx.tfs[setup_tf]
    if tfctx.ema21 is None or tfctx.ema50 is None or not tfctx.bars:
        return []
    last = tfctx.bars[-1]
    out: list[CandidateSetup] = []

    if tfctx.regime == "trend_up" and tfctx.ema21 > tfctx.ema50:
        if last.low <= tfctx.ema21 and last.close >= tfctx.ema21:
            entry = tfctx.ema21
            invalidation = tfctx.ema50
            out.append(CandidateSetup(
                type="pullback_ema",
                tf_setup=setup_tf,
                tf_trigger=trig_tf,
                side="long",
                entry_zone=EntryZone(
                    low=min(tfctx.ema21, last.low),
                    high=max(tfctx.ema21, last.close),
                ),
                suggested_entry=entry,
                invalidation=invalidation,
                targets=(),
                confluence=(f"{setup_tf}_EMA21_test", f"{setup_tf}_EMA_stack_up"),
                candle_quality=Decimal("0.6"),
                narrative_hint=f"{setup_tf} trend_up + price retesting EMA21",
            ))
    elif tfctx.regime == "trend_down" and tfctx.ema21 < tfctx.ema50:
        if last.high >= tfctx.ema21 and last.close <= tfctx.ema21:
            entry = tfctx.ema21
            invalidation = tfctx.ema50
            out.append(CandidateSetup(
                type="pullback_ema",
                tf_setup=setup_tf,
                tf_trigger=trig_tf,
                side="short",
                entry_zone=EntryZone(
                    low=min(tfctx.ema21, last.close),
                    high=max(tfctx.ema21, last.high),
                ),
                suggested_entry=entry,
                invalidation=invalidation,
                targets=(),
                confluence=(f"{setup_tf}_EMA21_test", f"{setup_tf}_EMA_stack_down"),
                candle_quality=Decimal("0.6"),
                narrative_hint=f"{setup_tf} trend_down + price retesting EMA21",
            ))
    return out


__all__ = ["detect"]
