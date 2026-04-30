"""End-to-end tests for ``trading_agent_skills.cli.guardian``."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from trading_agent_skills.cli.guardian import main


def _account(equity: str = "10000.00") -> dict:
    return {"equity": equity, "balance": equity, "currency": "USD"}


def _xauusd_position_entry(
    *,
    ticket: int = 1,
    sl: str = "2390.00",
    classification: str | None = None,
    reason: str = "",
    open_time: str = "2026-04-29T20:30:00+00:00",
) -> dict:
    return {
        "position": {
            "ticket": ticket,
            "symbol": "XAUUSD",
            "side": "long",
            "volume": "0.5",
            "price_open": "2400.00",
            "sl": sl,
            "tp": "2450.00",
            "price_current": "2400.00",
            "profit": "0",
            "swap": "0",
            "open_time": open_time,
        },
        "symbol": {"name": "XAUUSD", "tick_size": "0.01", "tick_value": "1.00"},
        "classification": classification,
        "classification_reason": reason,
    }


def _bundle(tmp_path: Path, **overrides) -> dict:
    base = {
        "now_utc": "2026-04-29T21:00:00+00:00",
        "account": _account(),
        "positions": [],
        "realized_pnl_today": "0.00",
        "config_path": str(tmp_path / "config.toml"),  # missing → defaults
        "state_path": str(tmp_path / "daily_state.json"),
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


# ---------- happy paths ----------------------------------------------------


def test_no_positions_no_realized_returns_clear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rc, result, err = _run(monkeypatch, _bundle(tmp_path))
    assert rc == 0, err
    assert result["status"] == "CLEAR"
    assert result["session_just_reset"] is True


def test_realized_loss_at_cap_halts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rc, result, _ = _run(
        monkeypatch, _bundle(tmp_path, realized_pnl_today="-500.00")
    )
    assert rc == 0
    assert result["status"] == "HALT"
    assert "DAILY_CAP_BREACHED" in result["flags"]


def test_at_risk_position_drawdown_summed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from decimal import Decimal

    rc, result, _ = _run(
        monkeypatch,
        _bundle(tmp_path, positions=[_xauusd_position_entry()]),
    )
    assert rc == 0
    assert Decimal(result["at_risk_combined_drawdown"]) == Decimal("500")


def test_classification_override_via_bundle_excludes_position(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from decimal import Decimal

    rc, result, _ = _run(
        monkeypatch,
        _bundle(tmp_path, positions=[
            _xauusd_position_entry(classification="RISK_FREE"),
        ]),
    )
    assert rc == 0
    assert Decimal(result["at_risk_combined_drawdown"]) == Decimal("0")


# ---------- session reset bookkeeping --------------------------------------


def test_session_state_persists_across_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_path = tmp_path / "state.json"
    bundle = _bundle(tmp_path, account=_account("10000.00"),
                     state_path=str(state_path))
    bundle["now_utc"] = "2026-04-29T21:00:00+00:00"
    rc1, r1, _ = _run(monkeypatch, bundle)
    assert r1["session_just_reset"] is True

    # Same session, different equity — must NOT reset.
    bundle2 = _bundle(tmp_path, account=_account("10250.00"),
                       state_path=str(state_path))
    bundle2["now_utc"] = "2026-04-29T22:00:00+00:00"
    rc2, r2, _ = _run(monkeypatch, bundle2)
    assert rc2 == 0
    assert r2["session_just_reset"] is False
    from decimal import Decimal as _D
    assert _D(r2["session_open_balance"]) == _D("10000")


def test_session_reset_after_4pm_ny_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_path = tmp_path / "state.json"
    # Day 1: open at 16:30 EDT.
    b1 = _bundle(tmp_path, account=_account("10000.00"),
                 state_path=str(state_path),
                 now_utc="2026-04-29T20:30:00+00:00")
    rc1, _, _ = _run(monkeypatch, b1)
    assert rc1 == 0
    # Day 2: 17:30 EDT next day — must reset, snapshotting current equity.
    b2 = _bundle(tmp_path, account=_account("10300.00"),
                 state_path=str(state_path),
                 now_utc="2026-04-30T21:30:00+00:00")
    rc2, r2, _ = _run(monkeypatch, b2)
    assert rc2 == 0
    assert r2["session_just_reset"] is True
    from decimal import Decimal as _D
    assert _D(r2["session_open_balance"]) == _D("10300")


# ---------- error handling -------------------------------------------------


def test_invalid_json_returns_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("{not json"))
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        rc = main([])
    assert rc == 1
    assert "invalid JSON" in err.getvalue()


def test_missing_account_returns_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _bundle(tmp_path)
    del bundle["account"]
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(bundle)))
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        rc = main([])
    assert rc == 1
    assert "malformed" in err.getvalue()
