"""End-to-end CLI test: feed a JSON bundle, parse the JSON output."""

import io
import json
from contextlib import redirect_stdout
from decimal import Decimal

import pytest

from trading_agent_skills.cli.size import main


def _bundle(**request_overrides) -> dict:
    request = {
        "side": "long",
        "risk_pct": "1.0",
        "stop_points": 200,
        "nights": 0,
    }
    request.update(request_overrides)
    return {
        "request": request,
        "account": {
            "equity": "10000",
            "margin_free": "9500",
            "leverage": 500,
            "currency": "USD",
        },
        "quote": {"bid": "1.0823", "ask": "1.0824"},
        "symbol": {
            "name": "EURUSD",
            "contract_size": "100000",
            "tick_size": "0.00001",
            "tick_value": "1",
            "volume_min": "0.01",
            "volume_max": "100",
            "volume_step": "0.01",
            "digits": 5,
            "calc_mode": "forex",
            "swap_mode": "by_points",
            "swap_long": "-2.5",
            "swap_short": "0.8",
            "margin_initial": "0",
            "stops_level": 0,
            "currency_profit": "USD",
            "currency_margin": "EUR",
        },
    }


def _run(monkeypatch, payload: dict) -> dict:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main([])
    assert rc == 0, f"non-zero exit: {rc}; output: {buf.getvalue()}"
    return json.loads(buf.getvalue())


def test_cli_eurusd_happy_path(monkeypatch):
    out = _run(monkeypatch, _bundle())
    assert out["symbol"] == "EURUSD"
    assert out["side"] == "long"
    assert out["lot_size"] == "0.50"
    assert out["cash_risk"] == "100.00"
    assert out["flags"] == []


def test_cli_decimals_serialise_as_fixed_point_strings(monkeypatch):
    out = _run(monkeypatch, _bundle())
    # No scientific notation in any Decimal field.
    for key in ("lot_size", "cash_risk", "notional", "stop_price"):
        assert "e" not in out[key].lower()


def test_cli_returns_2_when_request_invalid(monkeypatch):
    payload = _bundle()
    payload["request"].pop("risk_pct")
    payload["request"].pop("stop_points")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    err_buf = io.StringIO()
    out_buf = io.StringIO()
    monkeypatch.setattr("sys.stderr", err_buf)
    with redirect_stdout(out_buf):
        rc = main([])
    assert rc == 2
    assert "risk_pct or risk_amount" in err_buf.getvalue()


def test_cli_returns_1_when_json_invalid(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("{not json"))
    err_buf = io.StringIO()
    monkeypatch.setattr("sys.stderr", err_buf)
    rc = main([])
    assert rc == 1
    assert "invalid JSON" in err_buf.getvalue()


def test_cli_returns_1_when_schema_missing_keys(monkeypatch):
    payload = _bundle()
    del payload["account"]
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    err_buf = io.StringIO()
    monkeypatch.setattr("sys.stderr", err_buf)
    rc = main([])
    assert rc == 1
    assert "malformed input bundle" in err_buf.getvalue()
