"""Tests for AlphaVantage options OI crowdedness scoring (pure functions + CLI)."""

from __future__ import annotations

import io
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading_agent_skills.cot_crowdedness import Crowdedness
from trading_agent_skills.options_crowdedness import (
    AlphaVantageOptionsProvider,
    OptionsChainSnapshot,
    OptionsHistoryEntry,
    compute_crowdedness,
    put_call_oi_ratio,
)
from trading_agent_skills.cli import options_crowdedness as cli_mod


_AS_OF = datetime(2026, 5, 17, 16, 0, tzinfo=timezone.utc)


def _snap(
    *,
    call: int,
    put: int,
    expiry: date | None = None,
    dte: int = 30,
    symbol: str = "SPX500",
) -> OptionsChainSnapshot:
    if expiry is None:
        expiry = _AS_OF.date() + timedelta(days=dte)
    return OptionsChainSnapshot(
        as_of=_AS_OF,
        symbol=symbol,
        total_call_oi=Decimal(call),
        total_put_oi=Decimal(put),
        nearest_expiry=expiry,
        days_to_nearest_expiry=(expiry - _AS_OF.date()).days,
    )


def _history(ratios: list[str]) -> list[OptionsHistoryEntry]:
    base = _AS_OF - timedelta(days=len(ratios))
    return [
        OptionsHistoryEntry(
            as_of=base + timedelta(days=i),
            put_call_oi_ratio=Decimal(r),
        )
        for i, r in enumerate(ratios)
    ]


# ---------- put_call_oi_ratio ---------------------------------------------


def test_put_call_oi_ratio_basic_math() -> None:
    snap = _snap(call=1000, put=500)
    assert put_call_oi_ratio(snap) == Decimal("0.5")


def test_put_call_oi_ratio_floors_zero_calls() -> None:
    # call OI zero would divide by zero; we floor at 0.0001.
    snap = _snap(call=0, put=500)
    ratio = put_call_oi_ratio(snap)
    assert ratio == Decimal("500") / Decimal("0.0001")
    assert ratio > 0


# ---------- compute_crowdedness -------------------------------------------


def test_compute_crowdedness_crowded_short_high_pc_ratio() -> None:
    # 30 days of low p/c ratio, then a fresh spike.
    history = _history([f"{0.5 + i*0.001:.4f}" for i in range(30)])
    snap = _snap(call=100, put=900)  # p/c = 9 — way above history
    result = compute_crowdedness("SPX500", snap, history)
    assert result.tag == "crowded_short"
    assert result.percentile == Decimal("100")
    assert result.symbol == "SPX500"
    assert result.contract_label == "OPTIONS OI"
    assert result.inverse is False


def test_compute_crowdedness_crowded_long_low_pc_ratio() -> None:
    # History sits high (everyone hedged); current is unusually call-heavy.
    history = _history([f"{1.5 + i*0.001:.4f}" for i in range(30)])
    snap = _snap(call=1000, put=100)  # p/c = 0.1
    result = compute_crowdedness("SPX500", snap, history)
    assert result.tag == "crowded_long"
    # current ratio is the smallest in (history + current) → low percentile
    assert result.percentile <= Decimal("10")


def test_compute_crowdedness_neutral_when_in_middle() -> None:
    history = _history([f"{0.5 + i*0.05:.4f}" for i in range(30)])  # 0.5..1.95
    snap = _snap(call=1000, put=1200)  # ratio 1.2 — mid range
    result = compute_crowdedness("SPX500", snap, history)
    assert result.tag == "neutral"


def test_compute_crowdedness_pin_risk_flag_within_window() -> None:
    history = _history(["1.0"] * 10)
    snap = _snap(call=1000, put=1000, dte=3)
    result = compute_crowdedness("SPX500", snap, history, pin_risk_days=7)
    assert result.contract_code == "avopt:SPX500:pin"


def test_compute_crowdedness_no_pin_flag_outside_window() -> None:
    history = _history(["1.0"] * 10)
    snap = _snap(call=1000, put=1000, dte=21)
    result = compute_crowdedness("SPX500", snap, history, pin_risk_days=7)
    assert result.contract_code == "avopt:SPX500"
    assert not result.contract_code.endswith(":pin")


def test_compute_crowdedness_contract_code_carries_avopt_prefix() -> None:
    history = _history(["1.0"] * 5)
    snap = _snap(call=1000, put=1000, symbol="NAS100", dte=30)
    result = compute_crowdedness("NAS100", snap, history)
    assert result.contract_code.startswith("avopt:")
    assert "NAS100" in result.contract_code


def test_compute_crowdedness_latest_net_is_the_ratio() -> None:
    history = _history(["1.0"] * 10)
    snap = _snap(call=1000, put=2000)
    result = compute_crowdedness("SPX500", snap, history)
    assert result.latest_net == Decimal("2")


def test_compute_crowdedness_weeks_growing_counts_recent_growth() -> None:
    # Strictly rising p/c ratio history → all 4 deltas growing on short side.
    history = _history(["0.5", "0.6", "0.8", "1.0", "1.2", "1.5", "1.8", "2.0", "2.5", "3.0"])
    snap = _snap(call=100, put=400)  # current ratio 4 — extends crowded_short trend
    result = compute_crowdedness("SPX500", snap, history)
    assert result.tag == "crowded_short"
    assert result.weeks_growing == 4


def test_compute_crowdedness_returns_crowdedness_instance() -> None:
    history = _history(["1.0"] * 5)
    snap = _snap(call=1000, put=1000)
    result = compute_crowdedness("SPX500", snap, history)
    assert isinstance(result, Crowdedness)
    assert result.as_of == _AS_OF
    assert result.lookback_weeks == 5


# ---------- OptionsChainSnapshot.from_av_chain ----------------------------


def test_from_av_chain_aggregates_call_and_put_oi() -> None:
    blob = {
        "symbol": "SPY",
        "as_of": "2026-05-17T16:00:00+00:00",
        "data": [
            {"contract_type": "call", "open_interest": "1000", "expiration": "2026-06-19"},
            {"contract_type": "call", "open_interest": "500",  "expiration": "2026-06-19"},
            {"contract_type": "put",  "open_interest": "300",  "expiration": "2026-06-19"},
            {"contract_type": "put",  "open_interest": "700",  "expiration": "2026-07-17"},
        ],
    }
    snap = OptionsChainSnapshot.from_av_chain(blob)
    assert snap.total_call_oi == Decimal("1500")
    assert snap.total_put_oi == Decimal("1000")
    assert snap.symbol == "SPY"
    # nearest future expiry is 2026-06-19
    assert snap.nearest_expiry == date(2026, 6, 19)
    assert snap.days_to_nearest_expiry == (date(2026, 6, 19) - date(2026, 5, 17)).days


def test_from_av_chain_skips_malformed_rows() -> None:
    blob = {
        "symbol": "SPY",
        "as_of": "2026-05-17T16:00:00+00:00",
        "data": [
            {"contract_type": "call", "open_interest": "1000", "expiration": "2026-06-19"},
            {"contract_type": "call"},  # missing OI + expiration
            {"open_interest": "500", "expiration": "2026-06-19"},  # missing type
            {"contract_type": "warrant", "open_interest": "1", "expiration": "2026-06-19"},  # unknown type
            "not a dict",
            {"contract_type": "put", "open_interest": "bogus", "expiration": "2026-06-19"},  # bad OI
            {"contract_type": "put", "open_interest": "200", "expiration": "not-a-date"},  # bad date
            {"contract_type": "put", "open_interest": "400", "expiration": "2026-06-19"},
        ],
    }
    snap = OptionsChainSnapshot.from_av_chain(blob)
    assert snap.total_call_oi == Decimal("1000")
    assert snap.total_put_oi == Decimal("400")


def test_from_av_chain_handles_empty_data() -> None:
    blob = {"symbol": "SPY", "as_of": "2026-05-17T16:00:00+00:00", "data": []}
    snap = OptionsChainSnapshot.from_av_chain(blob)
    assert snap.total_call_oi == Decimal("0")
    assert snap.total_put_oi == Decimal("0")
    # nearest_expiry defaults to as_of date, days = 0
    assert snap.days_to_nearest_expiry == 0


# ---------- AlphaVantageOptionsProvider -----------------------------------


def test_provider_returns_none_when_snapshot_fn_is_none() -> None:
    provider = AlphaVantageOptionsProvider(
        snapshot_fn=None,
        history_fn=lambda sym: [],
    )
    assert provider.get_crowdedness("SPX500") is None


def test_provider_returns_none_when_snapshot_fn_returns_none() -> None:
    calls = []
    provider = AlphaVantageOptionsProvider(
        snapshot_fn=lambda sym: None,
        history_fn=lambda sym: calls.append(sym) or [],
    )
    assert provider.get_crowdedness("SPX500") is None
    # history_fn should not be invoked when snapshot is missing
    assert calls == []


def test_provider_composes_snapshot_and_history() -> None:
    snap = _snap(call=1000, put=1000)
    history = _history(["1.0"] * 5)
    provider = AlphaVantageOptionsProvider(
        snapshot_fn=lambda sym: snap,
        history_fn=lambda sym: history,
    )
    result = provider.get_crowdedness("SPX500")
    assert result is not None
    assert isinstance(result, Crowdedness)
    assert result.symbol == "SPX500"
    assert result.contract_code.startswith("avopt:SPX500")


# ---------- CLI -----------------------------------------------------------


def _cli_bundle() -> dict:
    return {
        "symbol": "SPX500",
        "as_of": "2026-05-17T16:00:00+00:00",
        "options_chain": {
            "data": [
                {"contract_type": "call", "open_interest": "1000",
                 "expiration": "2026-06-19"},
                {"contract_type": "put",  "open_interest": "500",
                 "expiration": "2026-06-19"},
            ],
        },
        "history": [
            {"as_of": "2026-04-19T16:00:00+00:00", "put_call_oi_ratio": "0.4"},
            {"as_of": "2026-04-26T16:00:00+00:00", "put_call_oi_ratio": "0.45"},
            {"as_of": "2026-05-03T16:00:00+00:00", "put_call_oi_ratio": "0.48"},
        ],
    }


def test_cli_pipes_bundle_via_stdin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = _cli_bundle()
    monkeypatch.setattr(sys_stdin := "sys.stdin", io.StringIO(json.dumps(bundle)))  # noqa: F841
    # Use the cli module's sys reference instead
    import sys as _sys
    monkeypatch.setattr(_sys, "stdin", io.StringIO(json.dumps(bundle)))
    rc = cli_mod.main([])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["symbol"] == "SPX500"
    assert payload["contract_code"].startswith("avopt:SPX500")
    assert payload["contract_label"] == "OPTIONS OI"
    assert payload["inverse"] is False
    assert "percentile" in payload
    assert "tag" in payload
    assert "latest_net" in payload


def test_cli_reads_input_file(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    bundle = _cli_bundle()
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(bundle))
    rc = cli_mod.main(["--input", str(path)])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["symbol"] == "SPX500"


def test_cli_returns_schema_error_on_bad_input(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import sys as _sys
    monkeypatch.setattr(_sys, "stdin", io.StringIO("{not json"))
    rc = cli_mod.main([])
    assert rc == 1
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["error"] == "schema_error"


def test_cli_returns_schema_error_when_missing_symbol(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import sys as _sys
    bundle = _cli_bundle()
    del bundle["symbol"]
    monkeypatch.setattr(_sys, "stdin", io.StringIO(json.dumps(bundle)))
    rc = cli_mod.main([])
    assert rc == 1
    out = capsys.readouterr().out
    assert json.loads(out)["error"] == "schema_error"
