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
    parse_response,
    refresh_symbol,
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


def test_fxssi_symbol_map_verified_oil_and_index_slugs() -> None:
    # Slugs verified 2026-05-17 against live /api/current-ratios payload —
    # guard against regressions if someone "tidies" them.
    assert FXSSI_SYMBOL_MAP["USOIL"] == "XTIUSD"
    assert FXSSI_SYMBOL_MAP["UKOIL"] == "XBRUSD"
    assert FXSSI_SYMBOL_MAP["SPX500"] == "SP500"
    assert FXSSI_SYMBOL_MAP["GER40"] == "GER40"
    assert FXSSI_SYMBOL_MAP["NAS100"] == "NAS100"
    assert FXSSI_SYMBOL_MAP["US30"] == "US30"


# ---------- parse_response (real-shape fixture) ---------------------------


def _real_shape_fixture() -> dict:
    """Cut-down version of /api/current-ratios payload — same shape as live."""
    return {
        "server_time": 1779013021,
        "formed": 1779012607,        # 2026-05-17T11:30:07Z
        "broker_titles": {"fxssi": "FXSSI", "myfxbook": "MyFxBook"},
        "pairs": {
            "XAUUSD": {
                "amarkets": "66.70", "dukscopy": "70.53", "fxssi": "82.44",
                "ftroanda": "90.13", "fxblue": "72.90", "instfor": "61.95",
                "myfxbook": "69.25", "xm": "57.30",
                "average": "74.82",
            },
            "XTIUSD": {  # = our USOIL
                "amarkets": "55.10", "fxssi": "62.00",
                "average": "58.55",
            },
            "BTCUSD": {
                "amarkets": "51.22", "fxssi": "71.61",
                "average": "52.54",
            },
            "ZZZZZZ": {"average": "99.00"},  # unmapped — should be skipped
            "BROKEN": "not-a-dict",          # malformed — should be skipped
        },
    }


def test_parse_response_extracts_mapped_pairs_only() -> None:
    parsed = parse_response(_real_shape_fixture())
    assert set(parsed) == {"XAUUSD", "USOIL", "BTCUSD"}    # ZZZZZZ + BROKEN dropped


def test_parse_response_uses_average_as_pct_long() -> None:
    parsed = parse_response(_real_shape_fixture())
    assert parsed["XAUUSD"].pct_long == Decimal("74.82")
    assert parsed["XAUUSD"].pct_short == Decimal("100") - Decimal("74.82")


def test_parse_response_translates_slug_to_our_symbol() -> None:
    parsed = parse_response(_real_shape_fixture())
    assert "USOIL" in parsed
    assert parsed["USOIL"].symbol == "USOIL"
    assert parsed["USOIL"].pct_long == Decimal("58.55")


def test_parse_response_uses_formed_timestamp() -> None:
    parsed = parse_response(_real_shape_fixture())
    # formed=1779012607 → 2026-05-17 10:10:07 UTC
    assert parsed["XAUUSD"].timestamp == datetime(2026, 5, 17, 10, 10, 7, tzinfo=timezone.utc)


def test_parse_response_rejects_missing_pairs_key() -> None:
    with pytest.raises(ValueError, match="missing 'pairs'"):
        parse_response({"server_time": 123})


def test_parse_response_rejects_bad_formed_value() -> None:
    with pytest.raises(ValueError, match="'formed' timestamp invalid"):
        parse_response({"pairs": {}, "formed": "not-a-number"})


def test_parse_response_falls_back_to_now_when_no_timestamp() -> None:
    # No formed, no server_time → uses datetime.now(UTC); we just assert it's tz-aware.
    parsed = parse_response({"pairs": {
        "XAUUSD": {"average": "60.00"},
    }})
    assert parsed["XAUUSD"].timestamp.tzinfo is not None


def test_xauusd_at_75_pct_long_is_crowded_long_via_real_shape() -> None:
    # End-to-end on the real-shape XAUUSD entry (74.82% long, just under
    # default 75 threshold → neutral). Tweak threshold to 74 to flip.
    parsed = parse_response(_real_shape_fixture())
    entries = [parsed["XAUUSD"]]
    snap = compute_crowdedness("XAUUSD", entries, long_threshold=Decimal("74"))
    assert snap.tag == "crowded_long"
    assert snap.percentile == Decimal("74.82")


# ---------- refresh_symbol with pre_fetched (no network) -----------------


def test_refresh_symbol_with_pre_fetched_skips_network(tmp_path: Path) -> None:
    parsed = parse_response(_real_shape_fixture())
    path, n = refresh_symbol("XAUUSD", cache_dir=tmp_path, pre_fetched=parsed)
    assert path.exists()
    assert n == 1


def test_refresh_symbol_merges_distinct_timestamps(tmp_path: Path) -> None:
    parsed = parse_response(_real_shape_fixture())

    # Seed cache with an older entry under a different timestamp.
    older = RetailSentimentEntry(
        timestamp=datetime(2026, 5, 16, 11, 30, 7, tzinfo=timezone.utc),
        symbol="XAUUSD",
        pct_long=Decimal("60"),
        pct_short=Decimal("40"),
        source="fxssi",
    )
    save_cache("XAUUSD", [older], cache_dir=tmp_path)

    path, n = refresh_symbol("XAUUSD", cache_dir=tmp_path, pre_fetched=parsed)
    assert n == 2

    loaded = load_cache("XAUUSD", cache_dir=tmp_path)
    assert {e.pct_long for e in loaded} == {Decimal("60"), Decimal("74.82")}


def test_refresh_symbol_dedupes_same_timestamp(tmp_path: Path) -> None:
    parsed = parse_response(_real_shape_fixture())
    refresh_symbol("XAUUSD", cache_dir=tmp_path, pre_fetched=parsed)
    # Second call with the same fetched bundle should NOT add a duplicate.
    _, n = refresh_symbol("XAUUSD", cache_dir=tmp_path, pre_fetched=parsed)
    assert n == 1


def test_refresh_symbol_raises_when_symbol_missing_from_response(tmp_path: Path) -> None:
    parsed = parse_response(_real_shape_fixture())   # has XAUUSD, USOIL, BTCUSD
    with pytest.raises(ValueError, match="did not contain"):
        refresh_symbol("GER40", cache_dir=tmp_path, pre_fetched=parsed)
