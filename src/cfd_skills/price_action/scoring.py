"""Deterministic structural quality score in [0, 1] for a CandidateSetup."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from cfd_skills.price_action.detectors import CandidateSetup
from cfd_skills.price_action.structure import MTFAlignment


@dataclass(frozen=True)
class ScoringWeights:
    confluence: Decimal
    mtf_alignment: Decimal
    candle_quality: Decimal
    freshness: Decimal


DEFAULT_WEIGHTS = ScoringWeights(
    confluence=Decimal("0.35"),
    mtf_alignment=Decimal("0.30"),
    candle_quality=Decimal("0.20"),
    freshness=Decimal("0.15"),
)

_MAX_CONFLUENCE = 4

_ALIGNMENT_SCORE: dict[str, Decimal] = {
    "aligned_long": Decimal("1.0"),
    "aligned_short": Decimal("1.0"),
    "mixed": Decimal("0.5"),
    "conflicted": Decimal("0.0"),
}


def _clamp(d: Decimal, lo: Decimal = Decimal("0"), hi: Decimal = Decimal("1")) -> Decimal:
    if d < lo:
        return lo
    if d > hi:
        return hi
    return d


def score_candidate(
    candidate: CandidateSetup,
    *,
    mtf_alignment: MTFAlignment,
    freshness_score: Decimal,
    weights: ScoringWeights = DEFAULT_WEIGHTS,
) -> Decimal:
    n_conf = min(len(candidate.confluence), _MAX_CONFLUENCE)
    confluence_norm = Decimal(n_conf) / Decimal(_MAX_CONFLUENCE)
    mtf_score = _ALIGNMENT_SCORE.get(mtf_alignment, Decimal("0"))
    quality = _clamp(candidate.candle_quality)
    fresh = _clamp(freshness_score)
    score = (
        weights.confluence * confluence_norm
        + weights.mtf_alignment * mtf_score
        + weights.candle_quality * quality
        + weights.freshness * fresh
    )
    return _clamp(score)


__all__ = ["ScoringWeights", "DEFAULT_WEIGHTS", "score_candidate"]
