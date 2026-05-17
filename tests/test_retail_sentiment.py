"""Tests for FXSSI retail-sentiment crowdedness (pure functions + cache roundtrip)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from trading_agent_skills.cot_crowdedness import Crowdedness
from trading_agent_skills.retail_sentiment import (
    FXSSI_SYMBOL_MAP,
    FxssiProvider,
    RetailSentimentEntry,
    compute_crowdedness,
    load_cache,
    save_cache,
)


def _entry(symbol: str, hours_ago: int, pct_long: int, pct_short: int) -> RetailSentimentEntry:
    return RetailSentimentEntry(
        timestamp=datetime(2026, 5, 17, 12, tzinfo=timezone.utc) - timedelta(hours=hours_ago),
        symbol=symbol,
        pct_long=Decimal(pct_long),
        pct_short=Decimal(pct_short),
        source="fxssi",
    )


# ---------- compute_crowdedness -------------------------------------------


def test_compute_crowdedness_crowded_long_at_threshold() -> None:
    entries = [_entry("GER40", hours_ago=0, pct_long=80, pct_short=20)]
    snap = compute_crowdedness("GER40", entries)
    assert snap.tag == "crowded_long"
    assert snap.percentile == Decimal("80")
    assert snap.inverse is False
    assert snap.contract_code == "fxssi:GER40"


def test_compute_crowdedness_crowded_short() -> None:
    entries = [_entry("UKOIL", hours_ago=0, pct_long=15, pct_short=85)]
    snap = compute_crowdedness("UKOIL", entries)
    assert snap.tag == "crowded_short"
    assert snap.percentile == Decimal("85")


def test_compute_crowdedness_neutral_when_balanced() -> None:
    entries = [_entry("EURUSD", hours_ago=0, pct_long=55, pct_short=45)]
    snap = compute_crowdedness("EURUSD", entries)
    assert snap.tag == "neutral"
    assert snap.percentile == Decimal("50")


def test_compute_crowdedness_just_below_threshold_is_neutral() -> None:
    entries = [_entry("XAUUSD", hours_ago=0, pct_long=74, pct_short=26)]
    snap = compute_crowdedness("XAUUSD", entries)
    assert snap.tag == "neutral"


def test_compute_crowdedness_contract_code_provider_prefixed() -> None:
    entries = [_entry("BTCUSD", hours_ago=0, pct_long=80, pct_short=20)]
    snap = compute_crowdedness("BTCUSD", entries)
    assert snap.contract_code.startswith("fxssi:")
    assert snap.contract_code == "fxssi:BTCUSD"


def test_compute_crowdedness_weeks_growing_counts_growing_side() -> None:
    # Five snapshots, pct_long climbing each one — last 4 deltas all positive.
    entries = [
        _entry("GER40", hours_ago=96, pct_long=70, pct_short=30),
        _entry("GER40", hours_ago=72, pct_long=75, pct_short=25),
        _entry("GER40", hours_ago=48, pct_long=78, pct_short=22),
        _entry("GER40", hours_ago=24, pct_long=80, pct_short=20),
        _entry("GER40", hours_ago=0,  pct_long=82, pct_short=18),
    ]
    snap = compute_crowdedness("GER40", entries)
    assert snap.tag == "crowded_long"
    assert snap.weeks_growing == 4


def test_compute_crowdedness_weeks_growing_adapts_to_short_series() -> None:
    # Only 2 entries — weeks_growing should still be computable (max 1).
    entries = [
        _entry("GER40", hours_ago=24, pct_long=78, pct_short=22),
        _entry("GER40", hours_ago=0,  pct_long=80, pct_short=20),
    ]
    snap = compute_crowdedness("GER40", entries)
    assert snap.tag == "crowded_long"
    assert snap.weeks_growing == 1


def test_compute_crowdedness_returns_crowdedness_instance() -> None:
    entries = [_entry("GER40", hours_ago=0, pct_long=80, pct_short=20)]
    snap = compute_crowdedness("GER40", entries)
    assert isinstance(snap, Crowdedness)
    # JSON-serialisable via as_dict
    json.dumps(snap.as_dict())


def test_compute_crowdedness_rejects_unknown_symbol() -> None:
    with pytest.raises(ValueError, match="no FXSSI mapping"):
        compute_crowdedness("ZZZZ", [_entry("ZZZZ", 0, 80, 20)])


def test_compute_crowdedness_rejects_empty_entries() -> None:
    with pytest.raises(ValueError, match="no retail-sentiment entries"):
        compute_crowdedness("GER40", [])


# ---------- cache roundtrip -----------------------------------------------


def test_save_and_load_cache_roundtrip(tmp_path: Path) -> None:
    entries = [
        _entry("GER40", hours_ago=48, pct_long=75, pct_short=25),
        _entry("GER40", hours_ago=24, pct_long=78, pct_short=22),
        _entry("GER40", hours_ago=0,  pct_long=80, pct_short=20),
    ]
    path = save_cache("GER40", entries, cache_dir=tmp_path)
    assert path.exists()

    loaded = load_cache("GER40", cache_dir=tmp_path)
    assert len(loaded) == 3
    expected = sorted(entries, key=lambda e: e.timestamp)
    for a, b in zip(loaded, expected):
        assert a.pct_long == b.pct_long
        assert a.pct_short == b.pct_short
        assert a.timestamp == b.timestamp
        assert a.source == b.source
        assert a.symbol == b.symbol


def test_load_cache_missing_returns_empty(tmp_path: Path) -> None:
    assert load_cache("GER40", cache_dir=tmp_path) == []


# ---------- FxssiProvider --------------------------------------------------


def test_fxssi_provider_returns_none_when_cache_missing(tmp_path: Path) -> None:
    provider = FxssiProvider(cache_dir=tmp_path)
    assert provider.get_crowdedness("GER40") is None


def test_fxssi_provider_returns_none_for_unmapped_symbol(tmp_path: Path) -> None:
    provider = FxssiProvider(cache_dir=tmp_path)
    assert provider.get_crowdedness("ZZZZ") is None


def test_fxssi_provider_reads_cache_and_computes(tmp_path: Path) -> None:
    entries = [_entry("GER40", hours_ago=0, pct_long=80, pct_short=20)]
    save_cache("GER40", entries, cache_dir=tmp_path)

    provider = FxssiProvider(cache_dir=tmp_path)
    snap = provider.get_crowdedness("GER40")
    assert snap is not None
    assert snap.tag == "crowded_long"
    assert snap.contract_code == "fxssi:GER40"


# ---------- Symbol map sanity --------------------------------------------


def test_fxssi_symbol_map_covers_expected_symbols() -> None:
    expected = {
        "GER40", "UKOIL", "XAUUSD", "XAGUSD", "USOIL",
        "NAS100", "SPX500", "US30",
        "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "EURGBP",
        "BTCUSD",
    }
    missing = expected - FXSSI_SYMBOL_MAP.keys()
    assert not missing, f"FXSSI_SYMBOL_MAP missing: {missing}"


def test_fxssi_symbol_map_slugs_nonempty() -> None:
    for sym, slug in FXSSI_SYMBOL_MAP.items():
        assert slug, f"{sym} has empty FXSSI slug"
