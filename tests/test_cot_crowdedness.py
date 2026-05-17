"""Tests for COT crowdedness scoring (pure functions + cache roundtrip)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from trading_agent_skills.cot_crowdedness import (
    SOCRATA_DISAGG,
    SOCRATA_LEGACY,
    SYMBOL_TO_CFTC,
    CotEntry,
    Crowdedness,
    compute_crowdedness,
    load_cache,
    percentile_rank,
    save_cache,
    tag_from_percentile,
)


def _entry(weeks_ago: int, mm_long: int, mm_short: int) -> CotEntry:
    return CotEntry(
        report_date=datetime(2026, 5, 12, tzinfo=timezone.utc) - timedelta(weeks=weeks_ago),
        contract_code="088691",
        mm_long=Decimal(mm_long),
        mm_short=Decimal(mm_short),
    )


# ---------- percentile_rank -----------------------------------------------


def test_percentile_rank_max_value() -> None:
    dist = [Decimal(i) for i in range(1, 11)]   # 1..10
    assert percentile_rank(Decimal(10), dist) == Decimal("100")


def test_percentile_rank_min_value() -> None:
    dist = [Decimal(i) for i in range(1, 11)]
    assert percentile_rank(Decimal(1), dist) == Decimal("10")


def test_percentile_rank_median_value() -> None:
    dist = [Decimal(i) for i in range(1, 11)]
    assert percentile_rank(Decimal(5), dist) == Decimal("50")


def test_percentile_rank_empty_raises() -> None:
    with pytest.raises(ValueError):
        percentile_rank(Decimal(5), [])


# ---------- tag_from_percentile -------------------------------------------


@pytest.mark.parametrize("pct, expected", [
    (Decimal("95"), "crowded_long"),
    (Decimal("90"), "crowded_long"),
    (Decimal("89.9"), "neutral"),
    (Decimal("50"), "neutral"),
    (Decimal("10"), "crowded_short"),
    (Decimal("5"), "crowded_short"),
])
def test_tag_from_percentile_thresholds(pct: Decimal, expected: str) -> None:
    assert tag_from_percentile(pct) == expected


# ---------- compute_crowdedness -------------------------------------------


def test_compute_crowdedness_crowded_long() -> None:
    # 50 weeks of flat 0-net positioning, then a top spike on the latest week.
    entries = [_entry(weeks_ago=50 - i, mm_long=100, mm_short=100) for i in range(50)]
    entries.append(_entry(weeks_ago=0, mm_long=10_000, mm_short=100))
    snap = compute_crowdedness("XAUUSD", entries)
    assert snap.tag == "crowded_long"
    assert snap.percentile == Decimal("100")
    assert snap.symbol == "XAUUSD"
    assert snap.contract_code == "088691"
    assert snap.inverse is False


def test_compute_crowdedness_crowded_short() -> None:
    entries = [_entry(weeks_ago=50 - i, mm_long=100, mm_short=100) for i in range(50)]
    entries.append(_entry(weeks_ago=0, mm_long=100, mm_short=10_000))
    snap = compute_crowdedness("XAUUSD", entries)
    assert snap.tag == "crowded_short"


def test_compute_crowdedness_neutral_when_in_middle() -> None:
    entries = [
        _entry(weeks_ago=50 - i, mm_long=100 + i * 10, mm_short=100) for i in range(50)
    ]
    entries.append(_entry(weeks_ago=0, mm_long=350, mm_short=100))  # mid range
    snap = compute_crowdedness("XAUUSD", entries)
    assert snap.tag == "neutral"


def test_compute_crowdedness_inverse_symbol_flips_tag() -> None:
    # USDJPY uses Japanese Yen futures inverted — crowded-long-JPY = crowded-short-USDJPY.
    entries = [
        CotEntry(
            report_date=datetime(2026, 5, 12, tzinfo=timezone.utc) - timedelta(weeks=50 - i),
            contract_code="097741",
            mm_long=Decimal(100), mm_short=Decimal(100),
        )
        for i in range(50)
    ]
    entries.append(CotEntry(
        report_date=datetime(2026, 5, 12, tzinfo=timezone.utc),
        contract_code="097741",
        mm_long=Decimal(10_000), mm_short=Decimal(100),
    ))
    snap = compute_crowdedness("USDJPY", entries)
    assert snap.inverse is True
    assert snap.tag == "crowded_short"   # JPY-long contract → USDJPY-short symbol


def test_compute_crowdedness_weeks_growing_counted() -> None:
    # Strictly growing net for the last 4 weeks → weeks_growing == 4.
    entries = []
    for i in range(50):
        entries.append(_entry(weeks_ago=50 - i, mm_long=100, mm_short=100))
    # Replace last 5 with a growing run that ends up crowded_long.
    entries[-5:] = [
        _entry(weeks_ago=4, mm_long=8_000, mm_short=100),
        _entry(weeks_ago=3, mm_long=9_000, mm_short=100),
        _entry(weeks_ago=2, mm_long=9_500, mm_short=100),
        _entry(weeks_ago=1, mm_long=10_000, mm_short=100),
        _entry(weeks_ago=0, mm_long=11_000, mm_short=100),
    ]
    snap = compute_crowdedness("XAUUSD", entries)
    assert snap.tag == "crowded_long"
    assert snap.weeks_growing == 4


def test_compute_crowdedness_rejects_unknown_symbol() -> None:
    with pytest.raises(ValueError, match="no CFTC mapping"):
        compute_crowdedness("BTCUSD", [_entry(0, 1, 1)])


def test_compute_crowdedness_rejects_empty_entries() -> None:
    with pytest.raises(ValueError, match="no COT entries"):
        compute_crowdedness("XAUUSD", [])


# ---------- cache roundtrip -----------------------------------------------


def test_save_and_load_cache_roundtrip(tmp_path: Path) -> None:
    entries = [_entry(weeks_ago=i, mm_long=1000 + i, mm_short=500) for i in range(5)]
    path = save_cache("XAUUSD", entries, cache_dir=tmp_path)
    assert path.exists()

    loaded = load_cache("XAUUSD", cache_dir=tmp_path)
    assert len(loaded) == len(entries)
    # save_cache sorts oldest-first
    expected = sorted(entries, key=lambda e: e.report_date)
    for a, b in zip(loaded, expected):
        assert a.mm_long == b.mm_long
        assert a.mm_short == b.mm_short
        assert a.contract_code == b.contract_code
        assert a.report_date == b.report_date


def test_load_cache_missing_returns_empty(tmp_path: Path) -> None:
    assert load_cache("XAUUSD", cache_dir=tmp_path) == []


# ---------- Crowdedness.as_dict serialisation -----------------------------


def test_crowdedness_as_dict_is_json_safe() -> None:
    entries = [_entry(weeks_ago=50 - i, mm_long=100 + i, mm_short=100) for i in range(50)]
    entries.append(_entry(weeks_ago=0, mm_long=10_000, mm_short=100))
    snap = compute_crowdedness("XAUUSD", entries)
    blob = snap.as_dict()
    # Should serialise without raising.
    json.dumps(blob)
    assert blob["symbol"] == "XAUUSD"
    assert blob["tag"] == "crowded_long"
    assert isinstance(blob["latest_net"], str)


# ---------- Symbol map sanity --------------------------------------------


def test_all_mapped_symbols_have_nonempty_contract_codes() -> None:
    for sym, c in SYMBOL_TO_CFTC.items():
        assert c.code, f"{sym} has empty contract code"
        assert c.label, f"{sym} has empty label"


# ---------- Dataset dispatch (disagg vs legacy) ---------------------------


def test_commodity_symbols_use_disagg_dataset() -> None:
    for sym in ("USOIL", "XAUUSD", "XAGUSD"):
        assert SYMBOL_TO_CFTC[sym].dataset == "disagg", \
            f"{sym} should be on Disaggregated dataset (commodity)"


def test_financial_symbols_use_legacy_dataset() -> None:
    for sym in ("NAS100", "SPX500", "US30",
                "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD"):
        assert SYMBOL_TO_CFTC[sym].dataset == "legacy", \
            f"{sym} should be on Legacy dataset (financial — Disaggregated has no coverage)"


def test_socrata_endpoint_urls_are_distinct() -> None:
    # Guard against accidental same-URL-different-name regression.
    assert SOCRATA_DISAGG != SOCRATA_LEGACY
    assert "72hh-3qpy" in SOCRATA_DISAGG
    assert "6dca-aqww" in SOCRATA_LEGACY


def test_from_socrata_disagg_reads_managed_money_fields() -> None:
    row = {
        "report_date_as_yyyy_mm_dd": "2026-05-12T00:00:00.000",
        "cftc_contract_market_code": "067651",
        "m_money_positions_long_all": "150000",
        "m_money_positions_short_all": "50000",
        # Legacy fields should be IGNORED when dataset="disagg":
        "noncomm_positions_long_all": "999",
        "noncomm_positions_short_all": "999",
    }
    entry = CotEntry.from_socrata(row, dataset="disagg")
    assert entry.mm_long == Decimal("150000")
    assert entry.mm_short == Decimal("50000")
    assert entry.mm_net == Decimal("100000")


def test_from_socrata_legacy_reads_noncomm_fields() -> None:
    row = {
        "report_date_as_yyyy_mm_dd": "2026-05-12T00:00:00.000",
        "cftc_contract_market_code": "099741",
        "noncomm_positions_long_all": "224002",
        "noncomm_positions_short_all": "183802",
        # Disaggregated fields should be IGNORED when dataset="legacy":
        "m_money_positions_long_all": "0",
        "m_money_positions_short_all": "0",
    }
    entry = CotEntry.from_socrata(row, dataset="legacy")
    assert entry.mm_long == Decimal("224002")
    assert entry.mm_short == Decimal("183802")
    assert entry.mm_net == Decimal("40200")


def test_from_socrata_default_dataset_is_disagg() -> None:
    # Back-compat: callers that don't pass dataset get Disaggregated semantics.
    row = {
        "report_date_as_yyyy_mm_dd": "2026-05-12T00:00:00.000",
        "cftc_contract_market_code": "067651",
        "m_money_positions_long_all": "100",
        "m_money_positions_short_all": "50",
    }
    entry = CotEntry.from_socrata(row)
    assert entry.mm_long == Decimal("100")
    assert entry.mm_short == Decimal("50")
