"""Tests for ``trading_agent_skills.calendar`` — pure functions, no I/O."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trading_agent_skills.calendar import enrich_events


NOW_UTC = datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)


def _econ_event(scheduled_at: str, *, actual: str | None = None) -> dict:
    base = {
        "id": "evt-1",
        "title": "CPI y/y",
        "currency": "USD",
        "impact": "High",
        "scheduledAt": scheduled_at,
        "forecast": "3.7%",
        "previous": "3.3%",
    }
    if actual is not None:
        base["actual"] = actual
    return base


def test_upcoming_event_gets_minutes_until_and_local_time(tmp_path) -> None:
    payload = {
        "updatedAt": "2026-05-14T11:00:00Z",
        "source": "tradays",
        "stale": False,
        "events": [_econ_event("2026-05-14T14:00:00Z")],  # 2h after NOW_UTC
    }
    out = enrich_events(payload, now_utc=NOW_UTC)
    e = out["events"][0]
    assert e["minutes_until"] == 120
    assert e["is_past"] is False
    assert "local_time_aest" in e
    assert "AEST" in e["local_time_aest"] or "AEDT" in e["local_time_aest"]


def test_now_utc_propagates_to_output(tmp_path) -> None:
    payload = {"updatedAt": "x", "source": "tradays", "stale": False, "events": []}
    out = enrich_events(payload, now_utc=NOW_UTC)
    assert out["now_utc"] == "2026-05-14T12:00:00+00:00"
    assert out["fetched_at_utc"] == "2026-05-14T12:00:00+00:00"


def test_stale_payload_sets_degraded_true(tmp_path) -> None:
    payload = {"updatedAt": "x", "source": "tradays", "stale": True, "events": []}
    out = enrich_events(payload, now_utc=NOW_UTC)
    assert out["degraded"] is True


def test_fresh_payload_sets_degraded_false(tmp_path) -> None:
    payload = {"updatedAt": "x", "source": "tradays", "stale": False, "events": []}
    out = enrich_events(payload, now_utc=NOW_UTC)
    assert out["degraded"] is False
