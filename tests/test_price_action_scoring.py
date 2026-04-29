"""Tests for the structural quality score."""

from __future__ import annotations

from decimal import Decimal

import pytest

from cfd_skills.price_action.detectors import CandidateSetup, EntryZone
from cfd_skills.price_action.scoring import (
    DEFAULT_WEIGHTS,
    ScoringWeights,
    score_candidate,
)


def _candidate(*, confluence: int, candle_quality: str) -> CandidateSetup:
    return CandidateSetup(
        type="fvg_fill",
        tf_setup="H4", tf_trigger="H1",
        side="long",
        entry_zone=EntryZone(low=Decimal("100"), high=Decimal("102")),
        suggested_entry=Decimal("101"),
        invalidation=Decimal("98"),
        targets=(),
        confluence=tuple(f"c{i}" for i in range(confluence)),
        candle_quality=Decimal(candle_quality),
        narrative_hint="x",
    )


def test_score_candidate_returns_decimal_in_unit_interval() -> None:
    c = _candidate(confluence=2, candle_quality="0.6")
    s = score_candidate(
        c, mtf_alignment="aligned_long", freshness_score=Decimal("0.8"),
    )
    assert isinstance(s, Decimal)
    assert Decimal("0") <= s <= Decimal("1")


def test_score_candidate_higher_with_more_confluence() -> None:
    low = score_candidate(
        _candidate(confluence=1, candle_quality="0.5"),
        mtf_alignment="aligned_long", freshness_score=Decimal("0.5"),
    )
    high = score_candidate(
        _candidate(confluence=4, candle_quality="0.5"),
        mtf_alignment="aligned_long", freshness_score=Decimal("0.5"),
    )
    assert high > low


def test_score_candidate_aligned_better_than_conflicted() -> None:
    aligned = score_candidate(
        _candidate(confluence=2, candle_quality="0.5"),
        mtf_alignment="aligned_long", freshness_score=Decimal("0.5"),
    )
    conflicted = score_candidate(
        _candidate(confluence=2, candle_quality="0.5"),
        mtf_alignment="conflicted", freshness_score=Decimal("0.5"),
    )
    assert aligned > conflicted


def test_score_candidate_respects_custom_weights() -> None:
    weights = ScoringWeights(
        confluence=Decimal("0"), mtf_alignment=Decimal("1"),
        candle_quality=Decimal("0"), freshness=Decimal("0"),
    )
    c = _candidate(confluence=4, candle_quality="0.0")
    s_long = score_candidate(
        c, mtf_alignment="aligned_long", freshness_score=Decimal("0"),
        weights=weights,
    )
    s_conf = score_candidate(
        c, mtf_alignment="conflicted", freshness_score=Decimal("0"),
        weights=weights,
    )
    assert s_long == Decimal("1")
    assert s_conf == Decimal("0")


def test_default_weights_sum_to_one() -> None:
    total = (
        DEFAULT_WEIGHTS.confluence
        + DEFAULT_WEIGHTS.mtf_alignment
        + DEFAULT_WEIGHTS.candle_quality
        + DEFAULT_WEIGHTS.freshness
    )
    assert total == Decimal("1")
