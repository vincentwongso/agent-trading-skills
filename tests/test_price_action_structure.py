"""Tests for ``cfd_skills.price_action.structure`` — S/R, regime, MTF alignment."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from cfd_skills.price_action.pivots import Pivot
from cfd_skills.price_action.structure import (
    MTFAlignment,
    RegimeKind,
    SRLevel,
    classify_mtf_alignment,
    classify_regime,
    cluster_sr_levels,
)


def _piv(i: int, price: str, kind: str, label: str | None = None) -> Pivot:
    return Pivot(
        index=i,
        time_utc=datetime(2026, 4, 1, tzinfo=timezone.utc),
        price=Decimal(price),
        kind=kind,  # type: ignore[arg-type]
        label=label,  # type: ignore[arg-type]
    )


def test_cluster_sr_levels_groups_within_tick_band() -> None:
    pivots = [
        _piv(0, "100.00", "swing_high"),
        _piv(2, "100.05", "swing_high"),
        _piv(4, "99.98", "swing_high"),
        _piv(6, "95.00", "swing_low"),
    ]
    levels = cluster_sr_levels(
        pivots, tick_size=Decimal("0.01"), cluster_factor=20, tf="H4"
    )
    assert len(levels) == 2
    high_cluster = next(l for l in levels if l.side == "resistance")
    assert high_cluster.tested == 3
    assert Decimal("99.98") <= high_cluster.price <= Decimal("100.05")
    low_cluster = next(l for l in levels if l.side == "support")
    assert low_cluster.tested == 1


def test_cluster_sr_levels_empty() -> None:
    assert cluster_sr_levels([], tick_size=Decimal("0.01"), cluster_factor=20, tf="H4") == []


def test_classify_regime_trend_up() -> None:
    pivots = [
        _piv(0, "100", "swing_low", None),
        _piv(2, "110", "swing_high", None),
        _piv(4, "105", "swing_low", "HL"),
        _piv(6, "115", "swing_high", "HH"),
        _piv(8, "108", "swing_low", "HL"),
        _piv(10, "120", "swing_high", "HH"),
    ]
    assert classify_regime(pivots) == "trend_up"


def test_classify_regime_trend_down() -> None:
    pivots = [
        _piv(0, "120", "swing_high", None),
        _piv(2, "110", "swing_low", None),
        _piv(4, "115", "swing_high", "LH"),
        _piv(6, "100", "swing_low", "LL"),
        _piv(8, "108", "swing_high", "LH"),
        _piv(10, "95", "swing_low", "LL"),
    ]
    assert classify_regime(pivots) == "trend_down"


def test_classify_regime_range_when_mixed() -> None:
    pivots = [
        _piv(0, "100", "swing_low", None),
        _piv(2, "110", "swing_high", None),
        _piv(4, "98", "swing_low", "LL"),
        _piv(6, "112", "swing_high", "HH"),
    ]
    assert classify_regime(pivots) == "range"


def test_classify_regime_transition_on_first_counter_pivot() -> None:
    pivots = [
        _piv(0, "100", "swing_low"),
        _piv(2, "110", "swing_high"),
        _piv(4, "105", "swing_low", "HL"),
        _piv(6, "115", "swing_high", "HH"),
        _piv(8, "108", "swing_low", "HL"),
        _piv(10, "112", "swing_high", "LH"),  # first counter — transition
    ]
    assert classify_regime(pivots) == "transition"


def test_classify_regime_empty_or_short() -> None:
    assert classify_regime([]) == "range"
    assert classify_regime([_piv(0, "100", "swing_low")]) == "range"


def test_classify_mtf_alignment_aligned_long() -> None:
    by_tf = {"D1": "trend_up", "H4": "trend_up", "H1": "trend_up"}
    assert classify_mtf_alignment(by_tf) == "aligned_long"


def test_classify_mtf_alignment_aligned_short() -> None:
    by_tf = {"D1": "trend_down", "H4": "trend_down", "H1": "trend_down"}
    assert classify_mtf_alignment(by_tf) == "aligned_short"


def test_classify_mtf_alignment_mixed_with_pullback() -> None:
    by_tf = {"D1": "trend_up", "H4": "trend_up", "H1": "transition"}
    assert classify_mtf_alignment(by_tf) == "mixed"


def test_classify_mtf_alignment_conflicted() -> None:
    by_tf = {"D1": "trend_up", "H4": "trend_down", "H1": "range"}
    assert classify_mtf_alignment(by_tf) == "conflicted"
