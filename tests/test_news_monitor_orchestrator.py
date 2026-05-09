"""End-to-end tests for monitor() orchestrator using stub clients."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx

from trading_agent_skills.news_clients import (
    AlphaVantageNewsClient,
    FinnhubClient,
    ForexNewsClient,
    MarketauxClient,
)
from trading_agent_skills.news_monitor import (
    NewsMonitorInput,
    NewsMonitorResult,
    SeverityThresholds,
    monitor,
)


def _ok_finnhub_handler(headline: str = "FOMC holds rates", summary: str = "Powell speaks."):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{
            "category": "general",
            "datetime": int(datetime.now(timezone.utc).timestamp()),
            "headline": headline,
            "id": 1,
            "image": "",
            "related": "USD",
            "source": "Reuters",
            "summary": summary,
            "url": "https://example.com/fomc",
        }])
    return handler


def _empty_handler(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"data": []})


def _make_clients(tmp_path: Path, finnhub_handler) -> dict:
    cache = tmp_path / "cache"
    return {
        "finnhub": FinnhubClient(
            cache_dir=cache,
            transport=httpx.MockTransport(finnhub_handler),
            api_key="test",
        ),
        "marketaux": MarketauxClient(
            cache_dir=cache,
            transport=httpx.MockTransport(_empty_handler),
            api_key=None,  # simulate missing key
        ),
        "forexnews": ForexNewsClient(
            cache_dir=cache,
            transport=httpx.MockTransport(_empty_handler),
            api_key=None,
        ),
        "alphavantage": AlphaVantageNewsClient(
            cache_dir=cache,
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"feed": []})),
            api_key=None,
        ),
    }


def test_monitor_emits_keyword_high_event(tmp_path: Path) -> None:
    state_path = tmp_path / "news_seen.jsonl"
    clients = _make_clients(tmp_path, _ok_finnhub_handler())
    inp = NewsMonitorInput(
        now_utc=datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc),
        lookback_minutes=10,
        state_path=state_path,
        state_ttl_hours=24,
        thresholds=SeverityThresholds(),
        clients=clients,
    )
    result: NewsMonitorResult = monitor(inp)
    assert len(result.events) == 1
    e = result.events[0]
    assert "FOMC" in e.headline
    assert e.impact == "high"
    assert e.severity_reason == "keyword"
    assert e.event_id  # populated, 16 chars
    assert state_path.exists()


def test_monitor_dedups_against_state(tmp_path: Path) -> None:
    state_path = tmp_path / "news_seen.jsonl"
    clients = _make_clients(tmp_path, _ok_finnhub_handler())
    inp = NewsMonitorInput(
        now_utc=datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc),
        lookback_minutes=10,
        state_path=state_path,
        state_ttl_hours=24,
        thresholds=SeverityThresholds(),
        clients=clients,
    )
    first = monitor(inp)
    assert len(first.events) == 1
    # Second call with same article should dedup
    second = monitor(inp)
    assert second.events == []


def test_monitor_skips_low_impact(tmp_path: Path) -> None:
    state_path = tmp_path / "news_seen.jsonl"
    clients = _make_clients(tmp_path, _ok_finnhub_handler(
        headline="Tesla unveils new color option",
        summary="Company announces cosmetic update.",
    ))
    inp = NewsMonitorInput(
        now_utc=datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc),
        lookback_minutes=10,
        state_path=state_path,
        state_ttl_hours=24,
        thresholds=SeverityThresholds(),
        clients=clients,
    )
    result = monitor(inp)
    assert result.events == []


def test_monitor_reports_provider_health(tmp_path: Path) -> None:
    state_path = tmp_path / "news_seen.jsonl"
    clients = _make_clients(tmp_path, _ok_finnhub_handler())
    inp = NewsMonitorInput(
        now_utc=datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc),
        lookback_minutes=10,
        state_path=state_path,
        state_ttl_hours=24,
        thresholds=SeverityThresholds(),
        clients=clients,
    )
    result = monitor(inp)
    assert result.provider_health["finnhub"] == "ok"
    assert result.provider_health["marketaux"] == "no_api_key"
    assert result.provider_health["forexnews"] == "no_api_key"
    assert result.provider_health["alphavantage"] == "no_api_key"


def test_monitor_all_providers_dead_emits_flag(tmp_path: Path) -> None:
    state_path = tmp_path / "news_seen.jsonl"
    def err(_r): return httpx.Response(503, text="down")
    cache = tmp_path / "cache"
    clients = {
        "finnhub": FinnhubClient(cache_dir=cache, transport=httpx.MockTransport(err), api_key="k"),
        "marketaux": MarketauxClient(cache_dir=cache, transport=httpx.MockTransport(err), api_key="k"),
        "forexnews": ForexNewsClient(cache_dir=cache, transport=httpx.MockTransport(err), api_key="k"),
        "alphavantage": AlphaVantageNewsClient(cache_dir=cache, transport=httpx.MockTransport(err), api_key="k"),
    }
    inp = NewsMonitorInput(
        now_utc=datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc),
        lookback_minutes=10,
        state_path=state_path,
        state_ttl_hours=24,
        thresholds=SeverityThresholds(),
        clients=clients,
    )
    result = monitor(inp)
    assert result.events == []
    assert "NEWS_PROVIDER_ALL_DEGRADED" in result.flags
