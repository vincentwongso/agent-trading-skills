"""Orchestrator: bundle → ScanContext → detectors → scoring → ScanResult."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from cfd_skills.price_action.bars import MTFBars, timeframe_minutes
from cfd_skills.price_action.context import ScanContext, build_context
from cfd_skills.price_action.detectors import CandidateSetup
from cfd_skills.price_action.detectors import bos_pullback as _bos
from cfd_skills.price_action.detectors import engulfing as _eng
from cfd_skills.price_action.detectors import fvg_fill as _fvg
from cfd_skills.price_action.detectors import liq_sweep as _liq
from cfd_skills.price_action.detectors import ob_retest as _ob
from cfd_skills.price_action.detectors import pin_bar as _pin
from cfd_skills.price_action.detectors import pullback_ema as _pull
from cfd_skills.price_action.detectors import range_break_retest as _rng
from cfd_skills.price_action.detectors import sr_bounce as _srb
from cfd_skills.price_action.schema import (
    EmaStackSnapshot,
    SCHEMA_VERSION,
    ScanResult,
)
from cfd_skills.price_action.scoring import (
    DEFAULT_WEIGHTS,
    ScoringWeights,
    score_candidate,
)


_DETECTORS = (
    ("pullback_ema", _pull.detect),
    ("sr_bounce", _srb.detect),
    ("pin_bar", _pin.detect),
    ("engulfing", _eng.detect),
    ("range_break_retest", _rng.detect),
    ("fvg_fill", _fvg.detect),
    ("ob_retest", _ob.detect),
    ("liq_sweep", _liq.detect),
    ("bos_pullback", _bos.detect),
)

_MIN_BARS_PER_TF = 60


@dataclass(frozen=True)
class ScanInput:
    symbol: str
    mode: str
    timeframes: tuple[str, ...]
    rates_by_tf: dict[str, list[dict[str, Any]]]
    current_price: Decimal
    tick_size: Decimal
    digits: int
    as_of: datetime
    max_setups: int = 3
    quality_threshold: Decimal = Decimal("0.45")
    cluster_factor: int = 20
    pivot_lookback: int = 3
    displacement_atr_mult: Decimal = Decimal("1.5")
    weights: ScoringWeights = DEFAULT_WEIGHTS


def _freshness(candidate: CandidateSetup, ctx: ScanContext) -> Decimal:
    sctx = ctx.tfs.get(candidate.tf_setup)
    if sctx is None or not sctx.bars:
        return Decimal("0.5")
    if any("FVG" in c or "OB" in c or "swept" in c for c in candidate.confluence):
        return Decimal("0.8")
    return Decimal("0.5")


def _scored_dict(
    setup_id: str, rank: int, candidate: CandidateSetup, score: Decimal,
) -> dict:
    return {
        "id": setup_id,
        "rank": rank,
        "type": candidate.type,
        "tf_setup": candidate.tf_setup,
        "tf_trigger": candidate.tf_trigger,
        "side": candidate.side,
        "entry_zone": {
            "low": candidate.entry_zone.low,
            "high": candidate.entry_zone.high,
        },
        "suggested_entry": candidate.suggested_entry,
        "invalidation": candidate.invalidation,
        "stop_distance": candidate.stop_distance,
        "targets": list(candidate.targets),
        "structural_score": score,
        "confluence": list(candidate.confluence),
        "narrative_hint": candidate.narrative_hint,
    }


def _ema_stack_snapshot(ctx: ScanContext) -> dict[str, EmaStackSnapshot]:
    out: dict[str, EmaStackSnapshot] = {}
    for tf, sctx in ctx.tfs.items():
        if sctx.ema21 is None or sctx.ema50 is None:
            out[tf] = EmaStackSnapshot(
                ema21=sctx.ema21, ema50=sctx.ema50,
                aligned=False, direction="none",
            )
            continue
        if sctx.ema21 > sctx.ema50:
            out[tf] = EmaStackSnapshot(
                ema21=sctx.ema21, ema50=sctx.ema50,
                aligned=True, direction="up",
            )
        elif sctx.ema21 < sctx.ema50:
            out[tf] = EmaStackSnapshot(
                ema21=sctx.ema21, ema50=sctx.ema50,
                aligned=True, direction="down",
            )
        else:
            out[tf] = EmaStackSnapshot(
                ema21=sctx.ema21, ema50=sctx.ema50,
                aligned=False, direction="none",
            )
    return out


def _bar_to_blob(b) -> dict:
    return {
        "time": b.time_utc.isoformat(),
        "open": b.open, "high": b.high, "low": b.low, "close": b.close,
        "volume": b.volume,
    }


def scan(inp: ScanInput) -> ScanResult:
    warnings: list[str] = []
    mtf = MTFBars.from_bundle(inp.rates_by_tf)
    for tf in inp.timeframes:
        if tf not in mtf.bars_by_tf:
            warnings.append(f"missing_bars_{tf}")
            continue
        if len(mtf.bars(tf)) < _MIN_BARS_PER_TF:
            warnings.append(f"sparse_bars_{tf}")

    ctx = build_context(
        symbol=inp.symbol, mtf=mtf,
        current_price=inp.current_price,
        tick_size=inp.tick_size, digits=inp.digits,
        cluster_factor=inp.cluster_factor,
        pivot_lookback=inp.pivot_lookback,
        displacement_atr_mult=inp.displacement_atr_mult,
    )

    candidates: list[CandidateSetup] = []
    for _, fn in _DETECTORS:
        candidates.extend(fn(ctx))

    scored: list[tuple[CandidateSetup, Decimal]] = []
    for c in candidates:
        s = score_candidate(
            c, mtf_alignment=ctx.mtf_alignment,
            freshness_score=_freshness(c, ctx),
            weights=inp.weights,
        )
        if s >= inp.quality_threshold:
            scored.append((c, s))

    scored.sort(key=lambda pair: pair[1], reverse=True)
    scored = scored[: inp.max_setups]

    setups = [
        _scored_dict(f"setup_{i + 1}", i + 1, c, s)
        for i, (c, s) in enumerate(scored)
    ]
    if not setups:
        warnings.append("no_clean_setup")

    if ctx.mtf_alignment == "conflicted":
        warnings.append("mtf_conflict")

    sorted_tfs = sorted(mtf.timeframes(), key=timeframe_minutes)
    recent: dict[str, list[dict]] = {}
    for tf in sorted_tfs[:2]:
        recent[tf] = [_bar_to_blob(b) for b in mtf.recent(tf, 20)]

    return ScanResult(
        schema_version=SCHEMA_VERSION,
        symbol=inp.symbol,
        mode=inp.mode,
        timeframes=tuple(inp.timeframes),
        as_of=inp.as_of,
        current_price=inp.current_price,
        regime={tf: ctx.tfs[tf].regime for tf in ctx.tfs},
        mtf_alignment=ctx.mtf_alignment,
        pivots_by_tf={tf: ctx.tfs[tf].pivots for tf in ctx.tfs},
        sr_levels=[lvl for s in ctx.tfs.values() for lvl in s.sr_levels],
        fvgs=[f for s in ctx.tfs.values() for f in s.fvgs],
        order_blocks=[o for s in ctx.tfs.values() for o in s.order_blocks],
        liquidity_pools=[p for s in ctx.tfs.values() for p in s.liquidity_pools],
        ema_stack=_ema_stack_snapshot(ctx),
        setups=setups,
        selected_setup_id=None,
        selection_rationale=None,
        warnings=warnings,
        recent_bars_window=recent,
    )


__all__ = ["ScanInput", "scan"]
