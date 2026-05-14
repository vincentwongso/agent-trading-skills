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
