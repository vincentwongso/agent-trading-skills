"""End-to-end tests for ``cfd_skills.cli.checklist``."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from pathlib import Path

import pytest

from cfd_skills.cli.checklist import main


def _bundle(tmp_path: Path, **overrides) -> dict:
    state_path = tmp_path / "daily_state.json"
    config_path = tmp_path / "config.toml"  # missing → defaults
    baseline_path = tmp_path / "spread_baseline.json"
    base = {
        "now_utc": "2026-04-29T21:00:00+00:00",
        "account": {"equity": "10000.00", "balance": "10000.00", "currency": "USD"},
        "positions": [],
        "realized_pnl_today": "0.00",
        "target": {
            "symbol": "XAUUSD",
            "side": "long",
            "candidate_risk_pct": "1.0",
        },
        "symbol_context": {
            "currency_base": "XAU",
            "currency_profit": "USD",
            "category": "metals",
            "market_open": True,
        },
        "calix": {
            "economic_events": [],
            "earnings_entries": [],
            "economic_stale": False,
            "earnings_stale": False,
        },
        "spread": {"current_pts": "12"},
        "config_path": str(config_path),
        "state_path": str(state_path),
        "spread_baseline_path": str(baseline_path),
    }
    base.update(overrides)
    return base


def _run(monkeypatch: pytest.MonkeyPatch, bundle: dict) -> tuple[int, dict, str]:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(bundle)))
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = main([])
    return rc, json.loads(out.getvalue() or "{}"), err.getvalue()


# ---------- happy path -----------------------------------------------------


def test_clean_state_returns_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rc, result, err = _run(monkeypatch, _bundle(tmp_path))
    assert rc == 0, err
    assert result["verdict"] == "PASS"
    assert result["symbol"] == "XAUUSD"
    assert result["side"] == "long"
    # Guardian sub-document is attached for rendering.
    assert result["guardian"]["status"] == "CLEAR"


def test_check_breakdown_includes_all_seven_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rc, result, _ = _run(monkeypatch, _bundle(tmp_path))
    assert rc == 0
    names = {c["name"] for c in result["checks"]}
    assert names == {
        "daily_risk", "concurrent_budget", "news_proximity",
        "earnings_proximity", "session", "exposure_overlap", "spread",
    }


# ---------- BLOCK paths ----------------------------------------------------


def test_market_closed_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _bundle(tmp_path)
    bundle["symbol_context"]["market_open"] = False
    rc, result, _ = _run(monkeypatch, bundle)
    assert rc == 0
    assert result["verdict"] == "BLOCK"


def test_daily_cap_breach_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rc, result, _ = _run(
        monkeypatch, _bundle(tmp_path, realized_pnl_today="-500.00")
    )
    assert rc == 0
    assert result["verdict"] == "BLOCK"
    assert result["guardian"]["status"] == "HALT"


# ---------- WARN paths -----------------------------------------------------


def test_calix_stale_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _bundle(tmp_path)
    bundle["calix"]["economic_stale"] = True
    rc, result, _ = _run(monkeypatch, bundle)
    assert rc == 0
    assert result["verdict"] == "WARN"


def test_news_within_30min_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _bundle(tmp_path)
    bundle["calix"]["economic_events"] = [
        {
            "id": "evt-1",
            "title": "FOMC Statement",
            "currency": "USD",
            "impact": "High",
            "scheduledAt": "2026-04-29T21:15:00Z",
            "forecast": None, "previous": None,
        },
    ]
    rc, result, _ = _run(monkeypatch, bundle)
    assert rc == 0
    assert result["verdict"] == "WARN"


def test_index_earnings_today_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _bundle(tmp_path)
    bundle["target"]["symbol"] = "NAS100"
    bundle["symbol_context"] = {
        "currency_base": "USD",
        "currency_profit": "USD",
        "category": "indices",
        "market_open": True,
    }
    bundle["calix"]["earnings_entries"] = [
        {
            "symbol": "AAPL", "name": "Apple Inc",
            "scheduledDate": "2026-04-29", "timing": "amc",
            "quarter": 2, "year": 2026,
        },
    ]
    rc, result, _ = _run(monkeypatch, bundle)
    assert rc == 0
    assert result["verdict"] == "WARN"


def test_spread_baseline_persists_across_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two consecutive calls — first bootstraps baseline, second compares."""
    baseline_path = tmp_path / "spread_baseline.json"
    state_path = tmp_path / "state.json"
    config_path = tmp_path / "config.toml"

    bundle1 = _bundle(tmp_path,
                      spread_baseline_path=str(baseline_path),
                      state_path=str(state_path),
                      config_path=str(config_path))
    bundle1["spread"]["current_pts"] = "10"
    rc1, r1, _ = _run(monkeypatch, bundle1)
    assert rc1 == 0
    assert baseline_path.exists()
    # First call: no prior baseline → PASS with bootstrap reason
    spread_check = next(c for c in r1["checks"] if c["name"] == "spread")
    assert spread_check["status"] == "PASS"
    assert "bootstrap" in spread_check["reason"].lower()

    # Second call: spread 30 pts vs ~10 baseline = 3x → WARN
    bundle2 = _bundle(tmp_path,
                      spread_baseline_path=str(baseline_path),
                      state_path=str(state_path),
                      config_path=str(config_path))
    bundle2["spread"]["current_pts"] = "30"
    rc2, r2, _ = _run(monkeypatch, bundle2)
    assert rc2 == 0
    spread_check2 = next(c for c in r2["checks"] if c["name"] == "spread")
    assert spread_check2["status"] == "WARN"
    assert Decimal(spread_check2["detail"]["ratio"]) >= Decimal("2")


# ---------- error handling -------------------------------------------------


def test_invalid_side_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _bundle(tmp_path)
    bundle["target"]["side"] = "hedge"
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(bundle)))
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        rc = main([])
    assert rc == 1
    assert "side" in err.getvalue().lower()


def test_missing_target_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _bundle(tmp_path)
    del bundle["target"]
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(bundle)))
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        rc = main([])
    assert rc == 1
