"""End-to-end CLI tests for trading-agent-skills-calendar.

Uses ``httpx.MockTransport`` to stub Calix responses without network.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from trading_agent_skills.cli.calendar import build_parser, run


def _stub_client_factory(handler):
    """Returns a ``client_factory`` callable matching ``run()``'s signature."""
    transport = httpx.MockTransport(handler)

    def factory(cache_dir: Path):
        from trading_agent_skills.calix_client import CalixClient
        return CalixClient(cache_dir=cache_dir, transport=transport)

    return factory


def test_parser_rejects_no_subcommand(capsys) -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_parser_rejects_economic_with_no_verb(capsys) -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["economic"])


def test_economic_upcoming_returns_enriched_json(tmp_path, capsys) -> None:
    from datetime import datetime, timezone

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/calendar/economic/upcoming"
        assert request.url.params["currencies"] == "USD"
        return httpx.Response(200, json={
            "updatedAt": "2026-05-14T12:00:00Z",
            "source": "tradays",
            "stale": False,
            "events": [
                {
                    "id": "evt-1", "title": "FOMC Statement", "currency": "USD",
                    "impact": "High", "scheduledAt": "2026-05-14T18:00:00Z",
                    "forecast": None, "previous": "5.50%",
                },
            ],
        })

    rc = run(
        ["economic", "upcoming", "--currencies", "USD", "--impact", "High", "--limit", "5"],
        now_utc=datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc),
        client_factory=_stub_client_factory(handler),
        cache_dir=tmp_path,
    )
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["events"][0]["minutes_until"] == 360  # 6h ahead
    assert out["events"][0]["is_past"] is False
    assert out["degraded"] is False


def test_within_hours_filters_out_far_events(tmp_path, capsys) -> None:
    from datetime import datetime, timezone

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "updatedAt": "x", "source": "tradays", "stale": False,
            "events": [
                {"id": "near", "title": "X", "currency": "USD", "impact": "High",
                 "scheduledAt": "2026-05-14T13:00:00Z", "forecast": None, "previous": None},
                {"id": "far",  "title": "Y", "currency": "USD", "impact": "High",
                 "scheduledAt": "2026-05-16T13:00:00Z", "forecast": None, "previous": None},
            ],
        })

    rc = run(
        ["economic", "upcoming", "--within-hours", "12"],
        now_utc=datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc),
        client_factory=_stub_client_factory(handler),
        cache_dir=tmp_path,
    )
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    ids = [e["id"] for e in out["events"]]
    assert ids == ["near"]


def test_raw_flag_emits_passthrough_payload(tmp_path, capsys) -> None:
    from datetime import datetime, timezone

    upstream = {
        "updatedAt": "x", "source": "tradays", "stale": False,
        "events": [{"id": "evt", "title": "X", "currency": "USD", "impact": "High",
                    "scheduledAt": "2026-05-14T18:00:00Z", "forecast": None, "previous": None}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=upstream)

    rc = run(
        ["economic", "upcoming", "--raw"],
        now_utc=datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc),
        client_factory=_stub_client_factory(handler),
        cache_dir=tmp_path,
    )
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out == upstream  # bytes-equal, no enrichment


def test_economic_past_returns_enriched_with_actuals(tmp_path, capsys) -> None:
    from datetime import datetime, timezone

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/calendar/economic/past"
        assert request.url.params["currencies"] == "USD"
        return httpx.Response(200, json={
            "updatedAt": "2026-05-14T12:00:00Z",
            "source": "tradays",
            "stale": False,
            "events": [
                {
                    "id": "ff_usd_cpi_y_y_20260512", "title": "CPI y/y",
                    "currency": "USD", "impact": "High",
                    "scheduledAt": "2026-05-12T12:30:00Z",
                    "forecast": "3.7%", "previous": "3.3%", "actual": "3.8%",
                },
            ],
        })

    rc = run(
        ["economic", "past", "--currencies", "USD"],
        now_utc=datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc),
        client_factory=_stub_client_factory(handler),
        cache_dir=tmp_path,
    )
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    e = out["events"][0]
    assert e["actual"] == "3.8%"
    assert e["actual_present"] is True
    assert e["is_past"] is True
    assert e["minutes_since"] > 0


def test_economic_find_cpi_on_specific_date(tmp_path, capsys) -> None:
    from datetime import datetime, timezone

    payload = {
        "updatedAt": "2026-05-14T12:00:00Z", "source": "tradays", "stale": False,
        "events": [
            {"id": "cpi-yy", "title": "CPI y/y", "currency": "USD", "impact": "High",
             "scheduledAt": "2026-05-12T12:30:00Z",
             "forecast": "3.7%", "previous": "3.3%", "actual": "3.8%"},
            {"id": "cpi-mm", "title": "CPI m/m", "currency": "USD", "impact": "High",
             "scheduledAt": "2026-05-12T12:30:00Z",
             "forecast": "0.7%", "previous": "0.9%", "actual": "0.6%"},
            {"id": "ppi-mm", "title": "PPI m/m", "currency": "USD", "impact": "High",
             "scheduledAt": "2026-05-13T12:30:00Z",
             "forecast": "0.4%", "previous": "0.7%", "actual": "1.4%"},
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/calendar/economic/past"
        # find passes --currency / --impact through to narrow the upstream query
        assert request.url.params["currencies"] == "USD"
        assert request.url.params["impact"] == "High"
        return httpx.Response(200, json=payload)

    rc = run(
        ["economic", "find", "--title", "CPI", "--currency", "USD", "--date", "2026-05-12"],
        now_utc=datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc),
        client_factory=_stub_client_factory(handler),
        cache_dir=tmp_path,
    )
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["query"] == {"title": "CPI", "currency": "USD", "date": "2026-05-12"}
    titles = sorted(m["title"] for m in out["matches"])
    assert titles == ["CPI m/m", "CPI y/y"]
    # PPI is not a near miss (different title); verify no false positives.
    assert all("PPI" not in m["title"] for m in out["matches"])


def test_economic_find_no_match_returns_empty_lists(tmp_path, capsys) -> None:
    from datetime import datetime, timezone

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "updatedAt": "x", "source": "tradays", "stale": False, "events": [],
        })

    rc = run(
        ["economic", "find", "--title", "UNICORN"],
        now_utc=datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc),
        client_factory=_stub_client_factory(handler),
        cache_dir=tmp_path,
    )
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["matches"] == []
    assert out["near_misses"] == []


def test_economic_find_without_currency_falls_back_to_all(tmp_path, capsys) -> None:
    from datetime import datetime, timezone

    def handler(request: httpx.Request) -> httpx.Response:
        # No --currency given → upstream currencies defaults to "all";
        # --impact still defaults to High only (not all four levels).
        assert request.url.params["currencies"] == "all"
        assert request.url.params["impact"] == "High"
        return httpx.Response(200, json={
            "updatedAt": "x", "source": "tradays", "stale": False, "events": [],
        })

    rc = run(
        ["economic", "find", "--title", "CPI"],
        now_utc=datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc),
        client_factory=_stub_client_factory(handler),
        cache_dir=tmp_path,
    )
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["matches"] == []


def test_earnings_upcoming_returns_enriched(tmp_path, capsys) -> None:
    from datetime import datetime, timezone

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/calendar/earnings/upcoming"
        return httpx.Response(200, json={
            "updatedAt": "x", "source": "finnhub", "stale": False,
            "earnings": [
                {"symbol": "NVDA", "name": "NVIDIA Corp",
                 "scheduledDate": "2026-05-20", "timing": "amc",
                 "quarter": 1, "year": 2026},
            ],
        })

    rc = run(
        ["earnings", "upcoming"],
        now_utc=datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc),
        client_factory=_stub_client_factory(handler),
        cache_dir=tmp_path,
    )
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["earnings"][0]["days_until"] == 6


def test_earnings_past_with_symbols_filter(tmp_path, capsys) -> None:
    from datetime import datetime, timezone

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/calendar/earnings/past"
        assert request.url.params["symbols"] == "AAPL,MSFT"
        return httpx.Response(200, json={
            "updatedAt": "x", "source": "finnhub", "stale": False,
            "earnings": [
                {"symbol": "AAPL", "name": "Apple Inc",
                 "scheduledDate": "2026-04-30", "timing": "amc",
                 "quarter": 2, "year": 2026,
                 "epsEstimate": 1.99, "epsActual": 2.01,
                 "revenueEstimate": 111853364107, "revenueActual": 111184000000},
            ],
        })

    rc = run(
        ["earnings", "past", "--symbols", "AAPL,MSFT", "--limit", "5"],
        now_utc=datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc),
        client_factory=_stub_client_factory(handler),
        cache_dir=tmp_path,
    )
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    e = out["earnings"][0]
    assert e["epsActual"] == 2.01
    assert e["actual_present"] is True
    assert e["is_past"] is True


def test_within_days_filters_out_far_earnings(tmp_path, capsys) -> None:
    from datetime import datetime, timezone

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "updatedAt": "x", "source": "finnhub", "stale": False,
            "earnings": [
                {"symbol": "NEAR", "name": "x", "scheduledDate": "2026-05-15",
                 "timing": "amc", "quarter": 1, "year": 2026},
                {"symbol": "FAR",  "name": "y", "scheduledDate": "2026-06-30",
                 "timing": "amc", "quarter": 2, "year": 2026},
            ],
        })

    rc = run(
        ["earnings", "upcoming", "--within-days", "7"],
        now_utc=datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc),
        client_factory=_stub_client_factory(handler),
        cache_dir=tmp_path,
    )
    out = json.loads(capsys.readouterr().out)
    assert [e["symbol"] for e in out["earnings"]] == ["NEAR"]


def test_calix_5xx_returns_exit_2_with_error_blob(tmp_path, capsys) -> None:
    from datetime import datetime, timezone

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream down")

    rc = run(
        ["economic", "upcoming"],
        now_utc=datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc),
        client_factory=_stub_client_factory(handler),
        cache_dir=tmp_path,
    )
    out = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert out["source"] == "calix"
    assert "503" in out["error"]


def test_calix_network_error_returns_exit_2(tmp_path, capsys) -> None:
    from datetime import datetime, timezone

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated network failure")

    rc = run(
        ["economic", "upcoming"],
        now_utc=datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc),
        client_factory=_stub_client_factory(handler),
        cache_dir=tmp_path,
    )
    out = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert out["source"] == "calix"


def test_stale_payload_marks_degraded_true(tmp_path, capsys) -> None:
    from datetime import datetime, timezone

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "updatedAt": "x", "source": "tradays", "stale": True, "events": [],
        })

    rc = run(
        ["economic", "upcoming"],
        now_utc=datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc),
        client_factory=_stub_client_factory(handler),
        cache_dir=tmp_path,
    )
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["degraded"] is True
