"""End-to-end tests for ``cfd_skills.cli.news``."""

from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cfd_skills.cli.news import (
    _derive_currencies_from_meta,
    _is_equity_like_ticker,
    main,
)
from cfd_skills.news_brief import SymbolMeta
from decimal import Decimal


@pytest.fixture(autouse=True)
def _isolate_news_api_keys():
    """``main()`` auto-loads ``./.env`` via ``os.environ.setdefault`` (so real
    shell env wins over the file). That bypasses monkeypatch's restore tracking,
    so the keys leak into other tests in the same pytest run. Save + restore
    around every test in this module."""
    keys = ("FINNHUB_API_KEY", "MARKETAUX_API_KEY", "FOREXNEWS_API_KEY")
    saved = {k: os.environ.pop(k, None) for k in keys}
    yield
    for k in keys:
        os.environ.pop(k, None)
        if saved[k] is not None:
            os.environ[k] = saved[k]


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


def _run(
    monkeypatch: pytest.MonkeyPatch, bundle: dict, argv: list[str] | None = None,
) -> tuple[int, dict, str]:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(bundle)))
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = main(argv if argv is not None else [])
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
    # Calendar tier intersects the editorial USD map with the bundle's
    # broker catalog (symbol_meta keys). Add USD-relevant entries so the
    # tier has something to surface.
    bundle["symbol_meta"]["XAUUSD.z"] = {
        "currency_base": "XAU",
        "currency_profit": "USD",
        "category": "metals",
        "swap_long": "-60",
        "swap_short": "40",
    }
    bundle["bars_by_symbol"]["XAUUSD.z"] = _bar_blobs(
        [str(2300 + i * 5) for i in range(25)]
    )
    rc, result, _ = _run(monkeypatch, bundle)
    assert rc == 0
    # XAUUSD.z is the broker form of editorial XAUUSD, so it appears verbatim:
    assert "XAUUSD.z" in result["watchlist_by_tier"]["calendar"]


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


# ---------- .env loading --------------------------------------------------


def test_env_file_flag_loads_keys_into_environ(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Make sure the keys are unset, then pass --env-file to a temp file that
    # sets one. After main() runs, os.environ should reflect the loaded value.
    # Note: the loader mutates os.environ via setdefault, which bypasses
    # monkeypatch's restore tracking — so we save+restore manually to keep
    # this test from leaking into the news_clients tests below.
    import os
    keys = ("FINNHUB_API_KEY", "MARKETAUX_API_KEY", "FOREXNEWS_API_KEY")
    saved = {k: os.environ.pop(k, None) for k in keys}
    try:
        env_path = tmp_path / "test.env"
        env_path.write_text("FINNHUB_API_KEY=loaded-from-dotenv\n", encoding="utf-8")
        bundle = _bundle(tmp_path)
        rc, _, _ = _run(monkeypatch, bundle, argv=["--env-file", str(env_path)])
        assert rc == 0
        assert os.environ.get("FINNHUB_API_KEY") == "loaded-from-dotenv"
    finally:
        for k in keys:
            os.environ.pop(k, None)
            if saved[k] is not None:
                os.environ[k] = saved[k]


# ---------- fan-out helpers (regression guards from smoke test) ------------


def _meta(base: str, profit: str, category: str = "metals") -> SymbolMeta:
    return SymbolMeta(
        symbol="X",
        currency_base=base,
        currency_profit=profit,
        category=category,
        swap_long=Decimal("0"),
        swap_short=Decimal("0"),
    )


class TestDeriveCurrenciesFromMeta:
    def test_extracts_3letter_codes_from_meta(self) -> None:
        meta = {
            "XAUUSD.z": _meta(base="XAU", profit="USD", category="metals"),
            "EURUSD.z": _meta(base="EUR", profit="USD", category="forex"),
        }
        out = _derive_currencies_from_meta(["XAUUSD.z", "EURUSD.z"], meta)
        assert out == {"XAU", "USD", "EUR"}

    def test_skips_symbols_with_no_meta(self) -> None:
        meta = {"UKOIL": _meta(base="UKOIL", profit="USD", category="commodities")}
        # UKOIL.currency_base is 5 letters → skipped. Only USD makes it.
        out = _derive_currencies_from_meta(["UKOIL", "PHANTOM"], meta)
        assert out == {"USD"}

    def test_handles_suffix_form_via_meta_lookup(self) -> None:
        """Smoke-test bug: the old length-6 heuristic missed XAUUSD.z (8 chars)
        and emitted no currencies → ForexNews schema_error."""
        meta = {"XAUUSD.z": _meta(base="XAU", profit="USD", category="metals")}
        out = _derive_currencies_from_meta(["XAUUSD.z"], meta)
        assert "USD" in out


class TestIsEquityLikeTicker:
    def test_accepts_short_alpha_uppercase(self) -> None:
        assert _is_equity_like_ticker("AAPL")
        assert _is_equity_like_ticker("MSFT")
        assert _is_equity_like_ticker("SPY")

    def test_rejects_suffix_form(self) -> None:
        assert not _is_equity_like_ticker("XAUUSD.z")
        assert not _is_equity_like_ticker("XAUUSD.Z")

    def test_rejects_symbols_with_digits(self) -> None:
        assert not _is_equity_like_ticker("NAS100")
        assert not _is_equity_like_ticker("US500")

    def test_rejects_long_or_lowercase(self) -> None:
        assert not _is_equity_like_ticker("XAUUSD")  # 6 chars, too long
        assert not _is_equity_like_ticker("aapl")


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
