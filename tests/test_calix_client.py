"""Tests for ``trading_agent_skills.calix_client`` — uses httpx MockTransport, no network."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from trading_agent_skills.calix_client import (
    CalixClient,
    CalixUnavailable,
)


def _ok_economic_payload(stale: bool = False) -> dict:
    return {
        "updatedAt": "2026-04-29T20:00:00Z",
        "source": "forexfactory",
        "stale": stale,
        "events": [
            {
                "id": "evt-1",
                "title": "FOMC Statement",
                "currency": "USD",
                "impact": "High",
                "scheduledAt": "2026-04-29T18:00:00Z",
                "forecast": "5.50%",
                "previous": "5.50%",
            },
        ],
    }


def _ok_earnings_payload(stale: bool = False) -> dict:
    return {
        "updatedAt": "2026-04-29T18:00:00Z",
        "source": "finnhub",
        "stale": stale,
        "earnings": [
            {
                "symbol": "AAPL",
                "name": "Apple Inc",
                "scheduledDate": "2026-04-30",
                "timing": "amc",
                "quarter": 2,
                "year": 2026,
            },
        ],
    }


def _client_with_handler(handler, tmp_path: Path, **kwargs) -> CalixClient:
    transport = httpx.MockTransport(handler)
    return CalixClient(
        cache_dir=tmp_path / "cache",
        transport=transport,
        **kwargs,
    )


# ---------- happy path -----------------------------------------------------


def test_fetch_economic_returns_payload_and_not_stale(tmp_path: Path) -> None:
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        assert request.url.params["currencies"] == "USD,EUR"
        assert request.url.params["impact"] == "High"
        assert request.url.params["limit"] == "10"
        return httpx.Response(200, json=_ok_economic_payload(stale=False))

    client = _client_with_handler(handler, tmp_path)
    resp = client.fetch_economic(currencies=["USD", "EUR"], impact=["High"], limit=10)
    assert resp.stale is False
    assert resp.degraded is False
    assert resp.cached is False
    assert resp.payload["events"][0]["title"] == "FOMC Statement"
    assert seen_paths == ["/v1/calendar/economic/upcoming"]


def test_fetch_earnings_returns_payload(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/calendar/earnings/upcoming"
        return httpx.Response(200, json=_ok_earnings_payload())

    client = _client_with_handler(handler, tmp_path)
    resp = client.fetch_earnings(limit=20)
    assert resp.payload["earnings"][0]["symbol"] == "AAPL"


def test_fetch_economic_accepts_string_currencies_alias(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["currencies"] == "majors"
        return httpx.Response(200, json=_ok_economic_payload())

    client = _client_with_handler(handler, tmp_path)
    client.fetch_economic(currencies="majors")


# ---------- staleness ------------------------------------------------------


def test_stale_payload_marks_response_degraded(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ok_economic_payload(stale=True))

    client = _client_with_handler(handler, tmp_path)
    resp = client.fetch_economic(currencies="majors")
    assert resp.stale is True
    assert resp.degraded is True


# ---------- caching --------------------------------------------------------


def test_repeat_call_within_ttl_uses_cache(tmp_path: Path) -> None:
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json=_ok_economic_payload())

    client = _client_with_handler(handler, tmp_path, cache_seconds=60)
    r1 = client.fetch_economic(currencies="majors")
    r2 = client.fetch_economic(currencies="majors")
    assert call_count["n"] == 1
    assert r1.cached is False
    assert r2.cached is True


def test_different_query_params_use_separate_cache_keys(tmp_path: Path) -> None:
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json=_ok_economic_payload())

    client = _client_with_handler(handler, tmp_path, cache_seconds=60)
    client.fetch_economic(currencies="USD")
    client.fetch_economic(currencies="EUR")
    assert call_count["n"] == 2


def test_cache_files_created_on_disk(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ok_economic_payload())

    cache_dir = tmp_path / "cache"
    client = CalixClient(
        cache_dir=cache_dir,
        transport=httpx.MockTransport(handler),
        cache_seconds=60,
    )
    client.fetch_economic(currencies="majors")
    cached_files = list(cache_dir.glob("*.json"))
    assert len(cached_files) == 1
    blob = json.loads(cached_files[0].read_text(encoding="utf-8"))
    assert blob["source"] == "forexfactory"


# ---------- failure modes --------------------------------------------------


def test_non_2xx_raises_calix_unavailable(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="degraded upstream")

    client = _client_with_handler(handler, tmp_path)
    with pytest.raises(CalixUnavailable, match="503"):
        client.fetch_economic(currencies="majors")


def test_transport_error_raises_calix_unavailable(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    client = _client_with_handler(handler, tmp_path)
    with pytest.raises(CalixUnavailable, match="failed"):
        client.fetch_economic(currencies="majors")


def test_non_json_response_raises_calix_unavailable(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json at all")

    client = _client_with_handler(handler, tmp_path)
    with pytest.raises(CalixUnavailable, match="non-JSON"):
        client.fetch_economic(currencies="majors")
