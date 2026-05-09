"""Tests for AlphaVantageNewsClient."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from trading_agent_skills.news_clients import AlphaVantageNewsClient


def _av_response(items: list[dict]) -> dict:
    return {
        "items": str(len(items)),
        "sentiment_score_definition": "...",
        "relevance_score_definition": "...",
        "feed": items,
    }


def _av_item(
    *,
    title: str = "Fed signals patience on cuts",
    url: str = "https://example.com/fed-patience",
    summary: str = "Powell at podium suggests no rush.",
    publisher: str = "Reuters",
    time_published: str = "20260509T013000",
    overall_sentiment_score: float = -0.42,
    overall_sentiment_label: str = "Bearish",
    ticker_sentiment: list[dict] | None = None,
) -> dict:
    return {
        "title": title,
        "url": url,
        "time_published": time_published,
        "summary": summary,
        "source": publisher,
        "category_within_source": "n/a",
        "overall_sentiment_score": overall_sentiment_score,
        "overall_sentiment_label": overall_sentiment_label,
        "ticker_sentiment": ticker_sentiment if ticker_sentiment is not None else [
            {"ticker": "FOREX:USD", "ticker_sentiment_score": "-0.42",
             "ticker_sentiment_label": "Bearish", "relevance_score": "0.81"},
        ],
    }


def _client(handler, tmp_path: Path) -> AlphaVantageNewsClient:
    return AlphaVantageNewsClient(
        cache_dir=tmp_path / "cache",
        transport=httpx.MockTransport(handler),
        api_key="test-key",
    )


def test_av_no_api_key_returns_empty(tmp_path: Path) -> None:
    client = AlphaVantageNewsClient(
        cache_dir=tmp_path / "cache",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
        api_key=None,
    )
    articles, status = client.fetch(topics=["economy_macro"], lookback_hours=1)
    assert articles == []
    assert status == "no_api_key"


def test_av_happy_path(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/query"
        assert request.url.params["function"] == "NEWS_SENTIMENT"
        assert "economy_macro" in request.url.params["topics"]
        return httpx.Response(200, json=_av_response([_av_item()]))

    client = _client(handler, tmp_path)
    articles, status = client.fetch(topics=["economy_macro"], lookback_hours=1)
    assert status == "ok"
    assert len(articles) == 1
    a = articles[0]
    assert a.title == "Fed signals patience on cuts"
    assert a.publisher == "Reuters"
    assert a.source == "alphavantage"
    assert a.sentiment_score == -0.42
    assert a.sentiment_label == "Bearish"
    assert a.relevance_score == 0.81
    assert "USD" in a.symbols  # FOREX:USD → USD


def test_av_5xx_returns_status(tmp_path: Path) -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="overloaded")

    articles, status = _client(handler, tmp_path).fetch(
        topics=["economy_macro"], lookback_hours=1,
    )
    assert articles == []
    assert status == "http_503"


def test_av_filters_by_lookback(tmp_path: Path) -> None:
    """time_from query param should reflect the lookback window."""
    seen_params: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_params.update(dict(request.url.params))
        return httpx.Response(200, json=_av_response([]))

    _client(handler, tmp_path).fetch(topics=["economy_macro"], lookback_hours=2)
    # AV format: YYYYMMDDTHHMM
    assert "time_from" in seen_params
    assert len(seen_params["time_from"]) == 13  # YYYYMMDDTHHMM
    assert seen_params["time_from"][8] == "T"


def test_av_caches_within_ttl(tmp_path: Path) -> None:
    calls = {"n": 0}

    def handler(_r: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_av_response([_av_item()]))

    client = _client(handler, tmp_path)
    client.fetch(topics=["economy_macro"], lookback_hours=1)
    client.fetch(topics=["economy_macro"], lookback_hours=1)
    assert calls["n"] == 1  # second call hits cache


def test_av_handles_missing_ticker_sentiment(tmp_path: Path) -> None:
    item = _av_item(ticker_sentiment=[])
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_av_response([item]))

    articles, _ = _client(handler, tmp_path).fetch(topics=["economy_macro"], lookback_hours=1)
    assert articles[0].symbols == ()
    assert articles[0].relevance_score is None
