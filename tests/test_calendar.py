"""Tests for ``trading_agent_skills.calendar`` — pure functions, no I/O."""

from __future__ import annotations

from datetime import datetime, timezone

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


def test_upcoming_event_gets_minutes_until_and_local_time() -> None:
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


def test_now_utc_propagates_to_output() -> None:
    payload = {"updatedAt": "x", "source": "tradays", "stale": False, "events": []}
    out = enrich_events(payload, now_utc=NOW_UTC)
    assert out["now_utc"] == "2026-05-14T12:00:00+00:00"
    assert out["fetched_at_utc"] == "2026-05-14T12:00:00+00:00"


def test_stale_payload_sets_degraded_true() -> None:
    payload = {"updatedAt": "x", "source": "tradays", "stale": True, "events": []}
    out = enrich_events(payload, now_utc=NOW_UTC)
    assert out["degraded"] is True


def test_fresh_payload_sets_degraded_false() -> None:
    payload = {"updatedAt": "x", "source": "tradays", "stale": False, "events": []}
    out = enrich_events(payload, now_utc=NOW_UTC)
    assert out["degraded"] is False


def test_past_event_gets_minutes_since_and_is_past_true() -> None:
    payload = {
        "updatedAt": "x", "source": "tradays", "stale": False,
        "events": [_econ_event("2026-05-14T11:30:00Z", actual="3.8%")],  # 30min ago
    }
    out = enrich_events(payload, now_utc=NOW_UTC)
    e = out["events"][0]
    assert e["minutes_until"] == -30
    assert e["minutes_since"] == 30
    assert e["is_past"] is True
    assert e["actual_present"] is True


def test_actual_present_false_when_actual_missing_or_null() -> None:
    payload = {
        "updatedAt": "x", "source": "tradays", "stale": False,
        "events": [
            _econ_event("2026-05-14T11:30:00Z"),  # no `actual` key
            {**_econ_event("2026-05-14T11:30:00Z"), "actual": None},
        ],
    }
    out = enrich_events(payload, now_utc=NOW_UTC)
    assert out["events"][0]["actual_present"] is False
    assert out["events"][1]["actual_present"] is False


def test_earnings_payload_uses_scheduledDate_not_scheduledAt() -> None:
    payload = {
        "updatedAt": "x", "source": "finnhub", "stale": False,
        "earnings": [
            {
                "symbol": "AAPL", "name": "Apple Inc",
                "scheduledDate": "2026-05-20", "timing": "amc",
                "quarter": 2, "year": 2026,
                "epsEstimate": 1.99, "epsActual": 2.01,
            },
        ],
    }
    out = enrich_events(payload, now_utc=NOW_UTC)
    e = out["earnings"][0]
    assert e["days_until"] == 6  # May 20 - May 14
    assert e["is_past"] is False
    assert e["actual_present"] is True


def test_past_earnings_with_actuals_set_present() -> None:
    payload = {
        "updatedAt": "x", "source": "finnhub", "stale": False,
        "earnings": [
            {
                "symbol": "MSFT", "name": "Microsoft Corp",
                "scheduledDate": "2026-04-29", "timing": "amc",
                "quarter": 3, "year": 2026,
                "epsEstimate": 4.14, "epsActual": 4.27,
                "revenueEstimate": 83020433323, "revenueActual": 82886000000,
            },
        ],
    }
    out = enrich_events(payload, now_utc=NOW_UTC)
    e = out["earnings"][0]
    assert e["days_since"] == 15  # May 14 - Apr 29
    assert e["is_past"] is True
    assert e["actual_present"] is True


def test_earnings_with_no_actuals_marks_present_false() -> None:
    payload = {
        "updatedAt": "x", "source": "finnhub", "stale": False,
        "earnings": [
            {
                "symbol": "GOOGL", "name": None,
                "scheduledDate": "2026-05-20", "timing": "unknown",
                "quarter": 1, "year": 2026,
            },
        ],
    }
    out = enrich_events(payload, now_utc=NOW_UTC)
    assert out["earnings"][0]["actual_present"] is False


from trading_agent_skills.calendar import find_events


def _events_fixture() -> list[dict]:
    return [
        _econ_event("2026-05-12T12:30:00Z", actual="3.8%") | {
            "id": "ff_usd_cpi_y_y_20260512", "title": "CPI y/y", "currency": "USD",
        },
        _econ_event("2026-05-12T12:30:00Z", actual="0.6%") | {
            "id": "ff_usd_cpi_m_m_20260512", "title": "CPI m/m", "currency": "USD",
        },
        _econ_event("2026-05-12T12:30:00Z", actual="0.4%") | {
            "id": "ff_usd_core_cpi_m_m_20260512", "title": "Core CPI m/m", "currency": "USD",
        },
        _econ_event("2026-05-13T12:30:00Z", actual="1.4%") | {
            "id": "ff_usd_ppi_m_m_20260513", "title": "PPI m/m", "currency": "USD",
        },
        _econ_event("2026-05-08T12:30:00Z", actual="115K") | {
            "id": "ff_usd_nonfarm_payrolls_20260508", "title": "Nonfarm Payrolls", "currency": "USD",
        },
    ]


def test_find_substring_match_returns_all_cpi_variants() -> None:
    result = find_events(_events_fixture(), title="CPI", currency=None, date=None)
    titles = [m["title"] for m in result["matches"]]
    assert sorted(titles) == ["CPI m/m", "CPI y/y", "Core CPI m/m"]
    assert result["near_misses"] == []


def test_find_with_currency_and_date_narrows_match() -> None:
    result = find_events(_events_fixture(), title="CPI", currency="USD", date="2026-05-12")
    titles = sorted(m["title"] for m in result["matches"])
    assert titles == ["CPI m/m", "CPI y/y", "Core CPI m/m"]


def test_find_returns_near_misses_when_date_doesnt_match() -> None:
    result = find_events(_events_fixture(), title="CPI", currency="USD", date="2026-05-15")
    assert result["matches"] == []
    near_titles = sorted(m["title"] for m in result["near_misses"])
    assert near_titles == ["CPI m/m", "CPI y/y", "Core CPI m/m"]


def test_find_no_match_no_near_miss_returns_empty_lists() -> None:
    result = find_events(_events_fixture(), title="UNICORN", currency=None, date=None)
    assert result["matches"] == []
    assert result["near_misses"] == []


def test_find_query_echoed_in_result() -> None:
    result = find_events(_events_fixture(), title="CPI", currency="USD", date="2026-05-12")
    assert result["query"] == {"title": "CPI", "currency": "USD", "date": "2026-05-12"}


def test_find_currency_match_is_case_insensitive() -> None:
    result = find_events(_events_fixture(), title="cpi", currency="usd", date=None)
    assert len(result["matches"]) == 3
