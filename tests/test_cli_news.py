"""End-to-end tests for ``cfd_skills.cli.news``."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cfd_skills.cli.news import main


def _bar_blobs(closes: list[str], spread: str = "1.0") -> list[dict]:
    base = datetime(2026, 4, 1, tzinfo=timezone.utc)
    out = []
    for i, c in enumerate(closes):
        out.append({
            "time": (base + timedelta(days=i)).isoformat(),
            "open": c,
            "high": str(float(c) + float(spread)),
            "low": str(float(c) - float(spread)),
            "close": c,
            "volume": 1000,
        })
    return out


def _bundle(tmp_path: Path, **overrides) -> dict:
    config_path = tmp_path / "config.toml"
    base = {
        "now_utc": "2026-04-29T21:00:00+00:00",
        "lookahead_hours": 4,
        "lookback_hours": 12,
        "explicit_watchlist": ["UKOIL"],
        "open_position_symbols": [],
        "calendar_event_currencies": [],
        "earnings_constituent_indices": [],
        "volatility_ranked": [],
        "max_size": 8,
        "symbol_meta": {
            "UKOIL": {
                "currency_base": "UKOIL",
                "currency_profit": "USD",
                "category": "commodities",
                "swap_long": "125",
                "swap_short": "-150",
            },
        },
        "bars_by_symbol": {
            "UKOIL": _bar_blobs([str(80 - i * 0.5) for i in range(25)]),
        },
        "calix": {
            "economic_events": [],
            "earnings_entries": [],
            "economic_stale": False,
            "earnings_stale": False,
        },
        "news": {
            "articles_by_provider": {
                "finnhub": [],
                "marketaux": [],
                "forexnews": [],
            },
            "provider_status": {
                "finnhub": "ok",
                "marketaux": "ok",
                "forexnews": "ok",
            },
        },
        "config_path": str(config_path),
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


def test_clean_run_returns_brief(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rc, result, err = _run(monkeypatch, _bundle(tmp_path))
    assert rc == 0, err
    assert "UKOIL" in result["watchlist"]
    # Swing candidate identified: UKOIL downtrend + positive long swap
    assert len(result["swing_candidates"]) == 1
    sc = result["swing_candidates"][0]
    assert sc["symbol"] == "UKOIL"
    assert sc["direction"] == "long_carry"


def test_calendar_overlay_in_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _bundle(tmp_path)
    bundle["calix"]["economic_events"] = [
        {
            "id": "evt-1", "title": "FOMC Statement",
            "currency": "USD", "impact": "High",
            "scheduledAt": "2026-04-29T22:00:00Z",
            "forecast": None, "previous": None,
        },
    ]
    rc, result, _ = _run(monkeypatch, bundle)
    assert rc == 0
    assert "UKOIL" in result["calendar_by_symbol"]


def test_news_grouped_by_symbol_via_currency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _bundle(tmp_path)
    bundle["news"]["articles_by_provider"]["finnhub"] = [
        {
            "title": "OPEC+ holds production cuts",
            "summary": "Group leaves output unchanged.",
            "url": "https://reuters.com/x",
            "source": "finnhub",
            "publisher": "Reuters",
            "symbols": ["UKOIL"],
            "keywords": [],
            "published_at_utc": "2026-04-29T18:00:00+00:00",
            "impact": "high",
        },
    ]
    rc, result, _ = _run(monkeypatch, bundle)
    assert rc == 0
    assert "UKOIL" in result["news_by_symbol"]
    assert result["news_by_symbol"]["UKOIL"][0]["title"] == "OPEC+ holds production cuts"


# ---------- watchlist resolution ------------------------------------------


def test_watchlist_falls_back_to_default_when_no_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _bundle(tmp_path)
    bundle["explicit_watchlist"] = []
    bundle["bars_by_symbol"] = {}  # default symbols won't have bars → flagged
    rc, result, _ = _run(monkeypatch, bundle)
    assert rc == 0
    # Default watchlist from config:
    assert "XAUUSD" in result["watchlist"]
    assert result["watchlist_by_tier"]["default"]


def test_calendar_event_currencies_drive_watchlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _bundle(tmp_path)
    bundle["explicit_watchlist"] = []
    bundle["calendar_event_currencies"] = ["USD"]
    rc, result, _ = _run(monkeypatch, bundle)
    assert rc == 0
    # USD-driven symbols should appear in calendar tier:
    assert any(s for s in result["watchlist_by_tier"]["calendar"])


# ---------- health flags --------------------------------------------------


def test_news_provider_no_api_key_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _bundle(tmp_path)
    bundle["news"]["provider_status"]["finnhub"] = "no_api_key"
    rc, result, _ = _run(monkeypatch, bundle)
    assert rc == 0
    assert "MISSING_NEWS_API_KEY" in result["flags"]
    assert result["health"]["finnhub"] == "no_api_key"


def test_calix_stale_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _bundle(tmp_path)
    bundle["calix"]["economic_stale"] = True
    rc, result, _ = _run(monkeypatch, bundle)
    assert rc == 0
    assert "CALIX_DEGRADED" in result["flags"]


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


def test_malformed_symbol_meta_returns_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _bundle(tmp_path)
    bundle["symbol_meta"]["UKOIL"]["swap_long"] = 125.0  # float, rejected by D
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(bundle)))
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        rc = main([])
    assert rc == 1
