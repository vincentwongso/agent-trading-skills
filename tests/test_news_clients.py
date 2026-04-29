"""Tests for ``cfd_skills.news_clients`` — Finnhub / Marketaux / ForexNews."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from cfd_skills.news_clients import (
    FinnhubClient,
    ForexNewsClient,
    MarketauxClient,
)


# ---------- shared helpers -------------------------------------------------


def _client(cls, handler, tmp_path: Path, **kwargs):
    return cls(
        cache_dir=tmp_path / "cache",
        transport=httpx.MockTransport(handler),
        api_key="test-key",
        **kwargs,
    )


# ---------- Finnhub --------------------------------------------------------


def _finnhub_blob(headline: str = "Fed holds rates",
                  source: str = "Reuters",
                  related: str = "USD",
                  ts: int = 1714398000) -> dict:
    return {
        "category": "general",
        "datetime": ts,
        "headline": headline,
        "id": 12345,
        "image": "",
        "related": related,
        "source": source,
        "summary": "FOMC keeps target unchanged.",
        "url": "https://reuters.com/markets/fed-holds",
    }


def test_finnhub_no_api_key_returns_empty(tmp_path: Path) -> None:
    client = FinnhubClient(cache_dir=tmp_path / "cache", api_key=None)
    articles, status = client.fetch_general()
    assert articles == []
    assert status == "no_api_key"


def test_finnhub_happy_path(tmp_path: Path) -> None:
    # Recent timestamp so it falls within the lookback.
    recent_ts = int(datetime.now(timezone.utc).timestamp()) - 60

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/news")
        assert request.url.params["category"] == "general"
        assert request.url.params["token"] == "test-key"
        return httpx.Response(200, json=[_finnhub_blob(ts=recent_ts)])

    client = _client(FinnhubClient, handler, tmp_path)
    articles, status = client.fetch_general(lookback_hours=12, limit=10)
    assert status == "ok"
    assert len(articles) == 1
    a = articles[0]
    assert a.source == "finnhub"
    assert a.publisher == "Reuters"
    assert a.title == "Fed holds rates"
    assert a.impact == "high"  # "Fed holds rates" → high keyword


def test_finnhub_filters_by_lookback(tmp_path: Path) -> None:
    old_ts = 1000000000  # year 2001 — definitely outside

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_finnhub_blob(ts=old_ts)])

    client = _client(FinnhubClient, handler, tmp_path)
    articles, status = client.fetch_general(lookback_hours=12)
    assert status == "ok"
    assert articles == []


def test_finnhub_5xx_returns_status(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="degraded upstream")

    client = _client(FinnhubClient, handler, tmp_path)
    articles, status = client.fetch_general()
    assert articles == []
    assert status == "http_503"


def test_finnhub_transport_error_returns_unavailable(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("DNS down", request=request)

    client = _client(FinnhubClient, handler, tmp_path)
    articles, status = client.fetch_general()
    assert articles == []
    assert status == "unavailable"


def test_finnhub_caches_within_ttl(tmp_path: Path) -> None:
    call_count = {"n": 0}
    recent_ts = int(datetime.now(timezone.utc).timestamp()) - 60

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json=[_finnhub_blob(ts=recent_ts)])

    client = _client(FinnhubClient, handler, tmp_path, cache_seconds=60)
    client.fetch_general()
    articles, status = client.fetch_general()
    assert call_count["n"] == 1
    assert status == "cache"
    assert len(articles) == 1


# ---------- Marketaux ------------------------------------------------------


def _marketaux_blob(title: str = "OPEC+ extends production cuts",
                    symbols: list[str] | None = None,
                    published: str = "2026-04-29T10:00:00Z") -> dict:
    return {
        "uuid": "abc-123",
        "title": title,
        "description": "OPEC and allies extend voluntary cuts.",
        "snippet": "Group leaves output unchanged.",
        "url": "https://reuters.com/commodities/opec-extends",
        "image_url": "",
        "language": "en",
        "published_at": published,
        "source": "Reuters",
        "relevance_score": 0.8,
        "entities": [
            {"symbol": s, "type": "equity"}
            for s in (symbols or ["USOIL", "UKOIL"])
        ],
    }


def test_marketaux_no_api_key(tmp_path: Path) -> None:
    client = MarketauxClient(cache_dir=tmp_path / "cache", api_key=None)
    articles, status = client.fetch(symbols=["USOIL"])
    assert status == "no_api_key"


def test_marketaux_happy_path(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/news/all")
        assert "symbols" in request.url.params
        return httpx.Response(200, json={"data": [_marketaux_blob()]})

    client = _client(MarketauxClient, handler, tmp_path)
    articles, status = client.fetch(symbols=["USOIL"], limit=10)
    assert status == "ok"
    assert articles[0].source == "marketaux"
    assert "USOIL" in articles[0].symbols


def test_marketaux_skips_symbols_param_when_empty(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "symbols" not in request.url.params
        return httpx.Response(200, json={"data": []})

    client = _client(MarketauxClient, handler, tmp_path)
    client.fetch(symbols=[])


def test_marketaux_schema_error_when_data_missing(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"meta": {}})  # no `data`

    client = _client(MarketauxClient, handler, tmp_path)
    articles, status = client.fetch(symbols=["AAPL"])
    assert articles == []
    assert status == "ok"  # `data: None` defaults to empty list


# ---------- ForexNews ------------------------------------------------------


def _forexnews_blob() -> dict:
    return {
        "news_url": "https://forexnewsapi.com/x/eur-usd-update",
        "image_url": "",
        "title": "EUR/USD steady ahead of ECB decision",
        "text": "Range-bound trading as traders await rate path signal.",
        "source_name": "FXStreet",
        "date": "2026-04-29 10:00:00 +0000",  # not strict ISO
        "topics": [],
        "sentiment": "Neutral",
        "type": "Article",
        "currency": ["EUR", "USD"],
        "image_full_path": "",
    }


def test_forexnews_no_api_key(tmp_path: Path) -> None:
    client = ForexNewsClient(cache_dir=tmp_path / "cache", api_key=None)
    articles, status = client.fetch(currencypairs=["EUR-USD"])
    assert status == "no_api_key"


def test_forexnews_happy_path(tmp_path: Path) -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["currencypair"] = request.url.params.get("currencypair", "")
        captured["currency"] = request.url.params.get("currency", "")
        return httpx.Response(200, json={"data": [_forexnews_blob()]})

    client = _client(ForexNewsClient, handler, tmp_path)
    articles, status = client.fetch(currencypairs=["EUR-USD", "XAU-USD"])
    assert status == "ok"
    a = articles[0]
    assert a.source == "forexnews"
    assert a.publisher == "FXStreet"
    assert "EUR" in a.keywords and "USD" in a.keywords
    # Bug #4 regression: API requires currencypair, rejects bare currency.
    assert captured["currencypair"] == "EUR-USD,XAU-USD"
    assert captured["currency"] == ""


def test_forexnews_skips_cleanly_when_no_input(tmp_path: Path) -> None:
    """The bare ``/api/v1`` endpoint without currencypair returns 301 →
    HTML error (parsed as schema_error after redirect). Skip with no_query
    instead so the orchestrator doesn't flag NEWS_PROVIDER_DEGRADED for
    a request we never sent."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"Should not have hit network: {request.url}")

    client = _client(ForexNewsClient, handler, tmp_path)
    articles, status = client.fetch()
    assert articles == []
    assert status == "no_query"


def test_forexnews_handles_non_iso_date(tmp_path: Path) -> None:
    """ForexNews returns "YYYY-MM-DD HH:MM:SS +0000" — must not crash."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [_forexnews_blob()]})

    client = _client(ForexNewsClient, handler, tmp_path)
    articles, status = client.fetch(currencypairs=["EUR-USD"])
    assert status == "ok"
    # Should not crash; falls back to "now" if parse fails.
    assert articles[0].published_at_utc is not None


def test_forexnews_follows_redirect(tmp_path: Path) -> None:
    """Round-2 smoke test bug: httpx default doesn't follow 301 → HTML body
    fails JSON parse. The base URL ``/api/v1`` redirects to ``/api/v1/`` and
    we must follow that for the API response to come through."""
    def handler(request: httpx.Request) -> httpx.Response:
        # First hop: base /api/v1 (no trailing slash) → 301
        if not request.url.path.endswith("/"):
            return httpx.Response(
                301,
                headers={"Location": str(request.url.copy_with(path=request.url.path + "/"))},
            )
        # Second hop: trailing slash → real JSON.
        return httpx.Response(200, json={"data": [_forexnews_blob()]})

    client = _client(ForexNewsClient, handler, tmp_path)
    articles, status = client.fetch(currencypairs=["EUR-USD"])
    assert status == "ok"
    assert len(articles) == 1


# ---------- shared cache behaviour ----------------------------------------


def test_cache_files_per_provider_dont_collide(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"

    def finnhub_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    def marketaux_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    f = FinnhubClient(
        cache_dir=cache_dir,
        transport=httpx.MockTransport(finnhub_handler),
        api_key="k",
    )
    m = MarketauxClient(
        cache_dir=cache_dir,
        transport=httpx.MockTransport(marketaux_handler),
        api_key="k",
    )
    f.fetch_general()
    m.fetch(symbols=["USOIL"])
    assert len(list(cache_dir.glob("*.json"))) == 2
