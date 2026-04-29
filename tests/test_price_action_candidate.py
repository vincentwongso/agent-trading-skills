"""Tests for the CandidateSetup dataclass and detector registry contract."""

from __future__ import annotations

from decimal import Decimal

import pytest

from cfd_skills.price_action.detectors import (
    CandidateSetup,
    EntryZone,
    SetupType,
)


def test_candidate_setup_required_fields() -> None:
    c = CandidateSetup(
        type="fvg_fill",
        tf_setup="H4",
        tf_trigger="H1",
        side="long",
        entry_zone=EntryZone(low=Decimal("100"), high=Decimal("102")),
        suggested_entry=Decimal("101"),
        invalidation=Decimal("98"),
        targets=(Decimal("110"), Decimal("115")),
        confluence=("H4_demand_FVG", "H1_swing_low"),
        candle_quality=Decimal("0.7"),
        narrative_hint="demo",
    )
    assert c.stop_distance == Decimal("3")
    assert c.side == "long"


def test_candidate_setup_short_stop_distance() -> None:
    c = CandidateSetup(
        type="liq_sweep",
        tf_setup="H1",
        tf_trigger="H1",
        side="short",
        entry_zone=EntryZone(low=Decimal("100"), high=Decimal("102")),
        suggested_entry=Decimal("101"),
        invalidation=Decimal("104"),
        targets=(Decimal("90"),),
        confluence=("BSL_swept",),
        candle_quality=Decimal("0.5"),
        narrative_hint="demo",
    )
    assert c.stop_distance == Decimal("3")


def test_entry_zone_validates_ordering() -> None:
    with pytest.raises(ValueError):
        EntryZone(low=Decimal("102"), high=Decimal("100"))
