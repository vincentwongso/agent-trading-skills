"""Tests for the cfd-skills-price-action CLI shim."""

from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from cfd_skills.cli.price_action import main


def _bar_blob(i: int, h: str, l: str, o: str, c: str) -> dict:
    t = datetime(2026, 4, 1, tzinfo=timezone.utc) + timedelta(hours=i)
    return {
        "time": t.isoformat(), "open": o, "high": h, "low": l, "close": c,
        "volume": 0,
    }


def _trend_up_bars() -> list[dict]:
    out = []
    p = Decimal("100")
    for i in range(80):
        p += Decimal("0.5")
        out.append(_bar_blob(
            i, str(p + Decimal("0.3")), str(p - Decimal("0.3")),
            str(p - Decimal("0.1")), str(p),
        ))
    return out


def _bundle() -> dict:
    bars = _trend_up_bars()
    return {
        "symbol": "XAUUSD",
        "mode": "swing",
        "timeframes": ["D1", "H4", "H1"],
        "as_of": "2026-04-05T00:00:00+00:00",
        "current_quote": {"bid": bars[-1]["close"], "ask": bars[-1]["close"], "time": "..."},
        "symbol_meta": {"tick_size": "0.01", "digits": 2,
                         "contract_size": "100", "trade_mode": "full"},
        "rates": {"D1": bars, "H4": bars, "H1": bars},
    }


def test_cli_price_action_smoke(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_bundle())))
    rc = main([])
    out = capsys.readouterr().out
    assert rc == 0
    parsed = json.loads(out)
    assert parsed["symbol"] == "XAUUSD"
    assert parsed["schema_version"] == "1.0"
    assert "setups" in parsed
    assert parsed["selected_setup_id"] is None


def test_cli_price_action_invalid_json(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    rc = main([])
    assert rc == 1


def test_cli_price_action_missing_symbol(monkeypatch, capsys) -> None:
    bundle = _bundle()
    del bundle["symbol"]
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(bundle)))
    rc = main([])
    assert rc == 1
