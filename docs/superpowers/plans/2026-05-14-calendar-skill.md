# Calendar Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `trading-agent-skills-calendar` — a CLI + SKILL.md on top of the existing `CalixClient` that exposes upcoming and past economic events and earnings, plus a `find` subcommand for "what did CPI print at" lookups. Solves the "agent hallucinates upcoming events" failure mode by giving agents a deterministic ground-truth path.

**Architecture:** Three new modules in `src/trading_agent_skills/`: extend `calix_client.py` with two `*_past()` methods, add a pure-function `calendar.py` for time enrichment + event finding, and a thin argparse front-end at `cli/calendar.py`. The existing `CalixClient` cache and `httpx.MockTransport` test pattern are reused — no refactor of consumers.

**Tech Stack:** Python 3.11+, `httpx` (already a dep), `argparse` (stdlib), `zoneinfo` (stdlib), `pytest` (dev dep).

---

## File map

| Path | Action | Responsibility |
|---|---|---|
| `src/trading_agent_skills/calix_client.py` | EDIT | Add `fetch_economic_past()` and `fetch_earnings_past()` methods. |
| `src/trading_agent_skills/calendar.py` | CREATE | Pure functions: `enrich_events`, `find_events`. No I/O, no global state. |
| `src/trading_agent_skills/cli/calendar.py` | CREATE | Argparse front-end. Three-level subcommands: `economic upcoming\|past\|find`, `earnings upcoming\|past`. |
| `tests/test_calix_client.py` | EDIT | Add coverage for the two new client methods. |
| `tests/test_calendar.py` | CREATE | Coverage for `enrich_events` + `find_events`. |
| `tests/test_cli_calendar.py` | CREATE | End-to-end CLI tests via `httpx.MockTransport`. |
| `pyproject.toml` | EDIT | Register `trading-agent-skills-calendar` entry point. |
| `.claude/skills/calendar/SKILL.md` | CREATE | Skill description, hard rules, workflow body. |
| `CHANGELOG.md` | EDIT | "Unreleased" entry for the new CLI + skill. |

**Conventions to preserve** (from `CLAUDE.md`):
- Conventional commit messages, **NO** `Co-Authored-By:` trailer.
- JSON-stdout for all CLI surface.
- No `unittest.mock` — fixture factories + `httpx.MockTransport`.
- Decimal-typed boundaries (n/a here — calendar deals only with strings + datetimes).

**Run all tests after each commit:**

```bash
.venv/bin/python -m pytest tests/ -q
```

(On Windows that's `.venv/Scripts/python.exe -m pytest tests/ -q`.)

---

## Task 1: `CalixClient.fetch_economic_past()`

**Files:**
- Modify: `src/trading_agent_skills/calix_client.py` (add method after `fetch_economic`)
- Test: `tests/test_calix_client.py` (add cases)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_calix_client.py` after the existing `test_fetch_economic_*` cases:

```python
def test_fetch_economic_past_hits_correct_path(tmp_path: Path) -> None:
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        assert request.url.params["currencies"] == "USD"
        assert request.url.params["impact"] == "High"
        assert request.url.params["limit"] == "10"
        return httpx.Response(200, json=_ok_economic_payload(stale=False))

    client = _client_with_handler(handler, tmp_path)
    resp = client.fetch_economic_past(currencies=["USD"], impact=["High"], limit=10)
    assert resp.stale is False
    assert seen_paths == ["/v1/calendar/economic/past"]


def test_fetch_economic_past_string_currencies_alias(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["currencies"] == "all"
        return httpx.Response(200, json=_ok_economic_payload())

    client = _client_with_handler(handler, tmp_path)
    client.fetch_economic_past(currencies="all")
```

- [ ] **Step 2: Run test to verify it fails**

```
.venv/bin/python -m pytest tests/test_calix_client.py::test_fetch_economic_past_hits_correct_path -v
```

Expected: FAIL with `AttributeError: 'CalixClient' object has no attribute 'fetch_economic_past'`.

- [ ] **Step 3: Implement the method**

In `src/trading_agent_skills/calix_client.py`, add immediately after the existing `fetch_economic` method:

```python
    def fetch_economic_past(
        self,
        *,
        currencies: Iterable[str] | str = "majors",
        impact: Iterable[str] = ("High",),
        limit: int = 10,
    ) -> CalixResponse:
        currencies_param: str
        if isinstance(currencies, str):
            currencies_param = currencies
        else:
            currencies_param = ",".join(currencies)
        return self._get(
            "/v1/calendar/economic/past",
            {
                "currencies": currencies_param,
                "impact": ",".join(impact),
                "limit": str(limit),
            },
        )
```

- [ ] **Step 4: Run tests to verify pass**

```
.venv/bin/python -m pytest tests/test_calix_client.py -q
```

Expected: PASS (all existing + new tests).

- [ ] **Step 5: Commit**

```bash
git add src/trading_agent_skills/calix_client.py tests/test_calix_client.py
git commit -m "feat(calix_client): add fetch_economic_past for past macro events"
```

---

## Task 2: `CalixClient.fetch_earnings_past()`

**Files:**
- Modify: `src/trading_agent_skills/calix_client.py`
- Test: `tests/test_calix_client.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_calix_client.py`:

```python
def test_fetch_earnings_past_hits_correct_path(tmp_path: Path) -> None:
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        return httpx.Response(200, json=_ok_earnings_payload())

    client = _client_with_handler(handler, tmp_path)
    resp = client.fetch_earnings_past(limit=20)
    assert resp.payload["earnings"][0]["symbol"] == "AAPL"
    assert seen_paths == ["/v1/calendar/earnings/past"]


def test_fetch_earnings_past_with_symbols_filter(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["symbols"] == "AAPL,MSFT"
        assert request.url.params["limit"] == "5"
        return httpx.Response(200, json=_ok_earnings_payload())

    client = _client_with_handler(handler, tmp_path)
    client.fetch_earnings_past(symbols=["AAPL", "MSFT"], limit=5)
```

- [ ] **Step 2: Run test to verify it fails**

```
.venv/bin/python -m pytest tests/test_calix_client.py::test_fetch_earnings_past_hits_correct_path -v
```

Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement**

The current `fetch_earnings` doesn't take `symbols`. Replace it AND add `fetch_earnings_past` so both expose the symbols filter consistently. In `src/trading_agent_skills/calix_client.py`, replace the existing `fetch_earnings` method and add the new past variant:

```python
    def fetch_earnings(
        self,
        *,
        symbols: Iterable[str] | str | None = None,
        limit: int = 20,
    ) -> CalixResponse:
        params: dict[str, str] = {"limit": str(limit)}
        if symbols is not None:
            params["symbols"] = symbols if isinstance(symbols, str) else ",".join(symbols)
        return self._get("/v1/calendar/earnings/upcoming", params)

    def fetch_earnings_past(
        self,
        *,
        symbols: Iterable[str] | str | None = None,
        limit: int = 20,
    ) -> CalixResponse:
        params: dict[str, str] = {"limit": str(limit)}
        if symbols is not None:
            params["symbols"] = symbols if isinstance(symbols, str) else ",".join(symbols)
        return self._get("/v1/calendar/earnings/past", params)
```

- [ ] **Step 4: Run tests**

```
.venv/bin/python -m pytest tests/test_calix_client.py -q
```

Expected: PASS. The existing `test_fetch_earnings_returns_payload` still passes because `symbols=None` matches the prior no-arg call.

- [ ] **Step 5: Commit**

```bash
git add src/trading_agent_skills/calix_client.py tests/test_calix_client.py
git commit -m "feat(calix_client): add fetch_earnings_past + symbols filter"
```

---

## Task 3: `enrich_events()` for upcoming events

**Files:**
- Create: `src/trading_agent_skills/calendar.py`
- Create: `tests/test_calendar.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_calendar.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```
.venv/bin/python -m pytest tests/test_calendar.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'trading_agent_skills.calendar'`.

- [ ] **Step 3: Implement**

Create `src/trading_agent_skills/calendar.py`:

```python
"""Pure-function enrichment + finding for Calix calendar payloads.

Inputs are raw Calix JSON dicts plus an injected ``now_utc`` so tests stay
deterministic. Outputs are augmented dicts with computed time fields. No I/O,
no module-level state, no Decimal (calendar deals in dates and strings).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


_SYDNEY = ZoneInfo("Australia/Sydney")  # auto-handles AEST/AEDT


def _parse_iso(s: str) -> datetime:
    """Calix returns ISO-8601 with trailing 'Z'; Python <3.11 doesn't grok 'Z'."""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _format_local(scheduled: datetime) -> str:
    local = scheduled.astimezone(_SYDNEY)
    return local.strftime("%Y-%m-%d %H:%M %Z")


def _enrich_economic(event: dict, now_utc: datetime) -> dict:
    enriched = dict(event)
    scheduled = _parse_iso(event["scheduledAt"])
    delta_minutes = int((scheduled - now_utc).total_seconds() // 60)
    enriched["minutes_until"] = delta_minutes
    enriched["minutes_since"] = -delta_minutes if delta_minutes < 0 else 0
    enriched["is_past"] = delta_minutes < 0
    enriched["local_time_aest"] = _format_local(scheduled)
    enriched["actual_present"] = event.get("actual") is not None
    return enriched


def enrich_events(payload: dict[str, Any], *, now_utc: datetime) -> dict[str, Any]:
    """Add computed time + presence fields to each event in a Calix payload.

    Output preserves all upstream fields and adds:
      - now_utc, fetched_at_utc (top-level)
      - degraded (top-level, mirrors stale)
      - per-event: minutes_until, minutes_since, is_past, local_time_aest,
        actual_present
    """
    iso_now = now_utc.isoformat()
    out = dict(payload)
    out["now_utc"] = iso_now
    out["fetched_at_utc"] = iso_now
    out["degraded"] = bool(payload.get("stale", False))
    if "events" in payload:
        out["events"] = [_enrich_economic(e, now_utc) for e in payload["events"]]
    return out
```

- [ ] **Step 4: Run test to verify pass**

```
.venv/bin/python -m pytest tests/test_calendar.py -v
```

Expected: PASS (all four cases).

- [ ] **Step 5: Commit**

```bash
git add src/trading_agent_skills/calendar.py tests/test_calendar.py
git commit -m "feat(calendar): add enrich_events for economic upcoming"
```

---

## Task 4: Extend `enrich_events()` for past events + earnings

**Files:**
- Modify: `src/trading_agent_skills/calendar.py`
- Modify: `tests/test_calendar.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_calendar.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```
.venv/bin/python -m pytest tests/test_calendar.py -v
```

Expected: FAIL — earnings cases don't have `days_until` / `actual_present`.

- [ ] **Step 3: Implement**

In `src/trading_agent_skills/calendar.py`, add an earnings enricher and update `enrich_events` to dispatch:

```python
def _enrich_earnings(entry: dict, now_utc: datetime) -> dict:
    enriched = dict(entry)
    # scheduledDate is YYYY-MM-DD; pin to UTC midnight for comparison.
    scheduled_date = datetime.fromisoformat(entry["scheduledDate"]).replace(tzinfo=timezone.utc)
    delta_days = (scheduled_date.date() - now_utc.date()).days
    enriched["days_until"] = delta_days
    enriched["days_since"] = -delta_days if delta_days < 0 else 0
    enriched["is_past"] = delta_days < 0
    enriched["actual_present"] = (
        entry.get("epsActual") is not None or entry.get("revenueActual") is not None
    )
    return enriched
```

Then update the `enrich_events` function to handle the earnings branch (replace its body):

```python
def enrich_events(payload: dict[str, Any], *, now_utc: datetime) -> dict[str, Any]:
    iso_now = now_utc.isoformat()
    out = dict(payload)
    out["now_utc"] = iso_now
    out["fetched_at_utc"] = iso_now
    out["degraded"] = bool(payload.get("stale", False))
    if "events" in payload:
        out["events"] = [_enrich_economic(e, now_utc) for e in payload["events"]]
    if "earnings" in payload:
        out["earnings"] = [_enrich_earnings(e, now_utc) for e in payload["earnings"]]
    return out
```

- [ ] **Step 4: Run tests to verify pass**

```
.venv/bin/python -m pytest tests/test_calendar.py -v
```

Expected: PASS (all 9 cases).

- [ ] **Step 5: Commit**

```bash
git add src/trading_agent_skills/calendar.py tests/test_calendar.py
git commit -m "feat(calendar): enrich past events + earnings entries"
```

---

## Task 5: `find_events()` pure function

**Files:**
- Modify: `src/trading_agent_skills/calendar.py`
- Modify: `tests/test_calendar.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_calendar.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```
.venv/bin/python -m pytest tests/test_calendar.py -v
```

Expected: FAIL with `ImportError: cannot import name 'find_events'`.

- [ ] **Step 3: Implement**

Append to `src/trading_agent_skills/calendar.py`:

```python
def find_events(
    events: list[dict],
    *,
    title: str,
    currency: str | None,
    date: str | None,
) -> dict[str, Any]:
    """Filter Calix economic events by case-insensitive title substring + optional
    currency + optional date.

    Returns ``{query, matches, near_misses}``:
      - matches: events satisfying ALL provided filters
      - near_misses: events that match title (+ currency if given) but NOT date,
        included only when ``date`` was specified
    """
    title_norm = title.lower()
    currency_norm = currency.upper() if currency else None

    title_currency_hits: list[dict] = []
    for e in events:
        if title_norm not in e["title"].lower():
            continue
        if currency_norm is not None and e["currency"].upper() != currency_norm:
            continue
        title_currency_hits.append(e)

    if date is None:
        matches = title_currency_hits
        near_misses: list[dict] = []
    else:
        matches = [e for e in title_currency_hits if e["scheduledAt"][:10] == date]
        match_ids = {e["id"] for e in matches}
        near_misses = [e for e in title_currency_hits if e["id"] not in match_ids]

    return {
        "query": {"title": title, "currency": currency, "date": date},
        "matches": matches,
        "near_misses": near_misses,
    }
```

- [ ] **Step 4: Run tests to verify pass**

```
.venv/bin/python -m pytest tests/test_calendar.py -v
```

Expected: PASS (all 15 cases).

- [ ] **Step 5: Commit**

```bash
git add src/trading_agent_skills/calendar.py tests/test_calendar.py
git commit -m "feat(calendar): add find_events for substring + currency + date lookups"
```

---

## Task 6: CLI skeleton — argparse structure + entry point

**Files:**
- Create: `src/trading_agent_skills/cli/calendar.py`
- Modify: `pyproject.toml`
- Create: `tests/test_cli_calendar.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_cli_calendar.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```
.venv/bin/python -m pytest tests/test_cli_calendar.py -v
```

Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Create the CLI skeleton**

Create `src/trading_agent_skills/cli/calendar.py`:

```python
"""CLI for the calendar skill — economic / earnings, upcoming / past / find.

Subcommand surface::

  trading-agent-skills-calendar economic upcoming  [filters]
  trading-agent-skills-calendar economic past      [filters]
  trading-agent-skills-calendar economic find      --title SUBSTR [--currency CODE] [--date YYYY-MM-DD]
  trading-agent-skills-calendar earnings upcoming  [filters]
  trading-agent-skills-calendar earnings past      [filters]

Output is JSON on stdout. Exit codes:
  0  success (including empty results)
  2  Calix unreachable / non-2xx / non-JSON  (also: argparse default for bad args)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from trading_agent_skills.calendar import enrich_events, find_events
from trading_agent_skills.calix_client import (
    DEFAULT_CACHE_DIR,
    CalixClient,
    CalixUnavailable,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trading-agent-skills-calendar",
        description="Look up upcoming / past economic events and earnings via Calix.",
    )
    noun = parser.add_subparsers(dest="noun", required=True)

    # economic
    econ = noun.add_parser("economic", help="Economic calendar events")
    econ_verb = econ.add_subparsers(dest="verb", required=True)

    for verb_name in ("upcoming", "past"):
        sub = econ_verb.add_parser(verb_name, help=f"{verb_name.title()} economic events")
        sub.add_argument("--currencies", default="majors")
        sub.add_argument("--impact", default="High")
        sub.add_argument("--limit", type=int, default=10)
        sub.add_argument("--within-hours", type=int, default=None)
        sub.add_argument("--raw", action="store_true")

    find_sub = econ_verb.add_parser("find", help="Find a specific past event by title")
    find_sub.add_argument("--title", required=True)
    find_sub.add_argument("--currency", default=None)
    find_sub.add_argument("--date", default=None, help="YYYY-MM-DD")
    find_sub.add_argument("--days-back", type=int, default=7)
    find_sub.add_argument("--raw", action="store_true")

    # earnings
    earn = noun.add_parser("earnings", help="Earnings releases")
    earn_verb = earn.add_subparsers(dest="verb", required=True)

    for verb_name in ("upcoming", "past"):
        sub = earn_verb.add_parser(verb_name, help=f"{verb_name.title()} earnings")
        sub.add_argument("--symbols", default=None)
        sub.add_argument("--limit", type=int, default=10)
        sub.add_argument("--within-days", type=int, default=None)
        sub.add_argument("--raw", action="store_true")

    return parser


ClientFactory = Callable[[Path], CalixClient]


def _default_client_factory(cache_dir: Path) -> CalixClient:
    return CalixClient(cache_dir=cache_dir)


def run(
    argv: list[str],
    *,
    now_utc: datetime | None = None,
    client_factory: ClientFactory | None = None,
    cache_dir: Path | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    now = now_utc or datetime.now(timezone.utc)
    factory = client_factory or _default_client_factory
    client = factory(cache_dir or DEFAULT_CACHE_DIR)

    # Dispatch table — populated in subsequent tasks.
    raise NotImplementedError(f"dispatch for {args.noun} {args.verb} not wired yet")


def main() -> int:
    return run(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
```

Now register the entry point in `pyproject.toml`. Find this block (line 20-29):

```toml
trading-agent-skills-strategy-review = "trading_agent_skills.cli.strategy_review:main"
```

Add after it:

```toml
trading-agent-skills-calendar = "trading_agent_skills.cli.calendar:main"
```

Reinstall in editable mode so the entry point registers:

```bash
.venv/bin/python -m pip install -e . --quiet
```

- [ ] **Step 4: Run test to verify pass**

```
.venv/bin/python -m pytest tests/test_cli_calendar.py -v
```

Expected: PASS (the two parser-rejection tests).

- [ ] **Step 5: Commit**

```bash
git add src/trading_agent_skills/cli/calendar.py tests/test_cli_calendar.py pyproject.toml
git commit -m "feat(cli): add calendar CLI skeleton + entry point"
```

---

## Task 7: Wire `economic upcoming` dispatch

**Files:**
- Modify: `src/trading_agent_skills/cli/calendar.py`
- Modify: `tests/test_cli_calendar.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_cli_calendar.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```
.venv/bin/python -m pytest tests/test_cli_calendar.py -v
```

Expected: FAIL with `NotImplementedError`.

- [ ] **Step 3: Implement dispatch + helper**

In `src/trading_agent_skills/cli/calendar.py`, replace the `raise NotImplementedError(...)` line at the bottom of `run()` with the dispatch logic, and add helper functions above `run()`:

```python
def _emit(payload: dict) -> int:
    print(json.dumps(payload, default=str))
    return 0


def _emit_calix_error(exc: CalixUnavailable) -> int:
    print(json.dumps({"error": str(exc), "source": "calix"}))
    return 2


def _filter_within_hours(payload: dict, hours: int | None, now_utc: datetime) -> dict:
    if hours is None or "events" not in payload:
        return payload
    cutoff_minutes = hours * 60
    payload = dict(payload)
    payload["events"] = [
        e for e in payload["events"]
        if e.get("minutes_until", 0) <= cutoff_minutes
    ]
    return payload


def _filter_within_days(payload: dict, days: int | None, now_utc: datetime) -> dict:
    if days is None or "earnings" not in payload:
        return payload
    payload = dict(payload)
    payload["earnings"] = [
        e for e in payload["earnings"]
        if e.get("days_until", 0) <= days
    ]
    return payload


def _impact_list(s: str) -> list[str]:
    return [tok.strip() for tok in s.split(",") if tok.strip()]
```

Then replace the `raise NotImplementedError(...)` line with:

```python
    try:
        if args.noun == "economic" and args.verb == "upcoming":
            resp = client.fetch_economic(
                currencies=args.currencies,
                impact=_impact_list(args.impact),
                limit=args.limit,
            )
            if args.raw:
                return _emit(resp.payload)
            enriched = enrich_events(resp.payload, now_utc=now)
            enriched = _filter_within_hours(enriched, args.within_hours, now)
            return _emit(enriched)
    except CalixUnavailable as exc:
        return _emit_calix_error(exc)

    raise NotImplementedError(f"dispatch for {args.noun} {args.verb} not wired yet")
```

- [ ] **Step 4: Run tests to verify pass**

```
.venv/bin/python -m pytest tests/test_cli_calendar.py -v
```

Expected: PASS for the three new cases (parser tests still pass).

- [ ] **Step 5: Commit**

```bash
git add src/trading_agent_skills/cli/calendar.py tests/test_cli_calendar.py
git commit -m "feat(cli-calendar): wire economic upcoming dispatch + within-hours filter"
```

---

## Task 8: Wire `economic past` dispatch

**Files:**
- Modify: `src/trading_agent_skills/cli/calendar.py`
- Modify: `tests/test_cli_calendar.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_cli_calendar.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```
.venv/bin/python -m pytest tests/test_cli_calendar.py::test_economic_past_returns_enriched_with_actuals -v
```

Expected: FAIL with `NotImplementedError`.

- [ ] **Step 3: Add the dispatch branch**

In `src/trading_agent_skills/cli/calendar.py`, inside `run()`'s `try` block, add immediately after the `economic upcoming` branch (before the `except`):

```python
        if args.noun == "economic" and args.verb == "past":
            resp = client.fetch_economic_past(
                currencies=args.currencies,
                impact=_impact_list(args.impact),
                limit=args.limit,
            )
            if args.raw:
                return _emit(resp.payload)
            enriched = enrich_events(resp.payload, now_utc=now)
            return _emit(enriched)
```

- [ ] **Step 4: Run tests to verify pass**

```
.venv/bin/python -m pytest tests/test_cli_calendar.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/trading_agent_skills/cli/calendar.py tests/test_cli_calendar.py
git commit -m "feat(cli-calendar): wire economic past dispatch"
```

---

## Task 9: Wire `economic find` dispatch

**Files:**
- Modify: `src/trading_agent_skills/cli/calendar.py`
- Modify: `tests/test_cli_calendar.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_cli_calendar.py`:

```python
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
        # find broadens the request — currencies=all, impact=all-levels
        assert request.url.params["currencies"] == "all"
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
```

- [ ] **Step 2: Run tests to verify they fail**

```
.venv/bin/python -m pytest tests/test_cli_calendar.py -v
```

Expected: FAIL with `NotImplementedError`.

- [ ] **Step 3: Add the dispatch branch**

In `src/trading_agent_skills/cli/calendar.py`, add this branch in `run()`'s `try` block after the `economic past` branch:

```python
        if args.noun == "economic" and args.verb == "find":
            # Broaden the upstream request so we never miss a candidate;
            # narrow client-side via find_events.
            resp = client.fetch_economic_past(
                currencies="all",
                impact=["High", "Medium", "Low", "Holiday"],
                limit=25,
            )
            result = find_events(
                resp.payload.get("events", []),
                title=args.title,
                currency=args.currency,
                date=args.date,
            )
            result["fetched_at_utc"] = now.isoformat()
            result["source"] = resp.payload.get("source")
            result["stale"] = bool(resp.payload.get("stale", False))
            return _emit(result)
```

- [ ] **Step 4: Run tests to verify pass**

```
.venv/bin/python -m pytest tests/test_cli_calendar.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/trading_agent_skills/cli/calendar.py tests/test_cli_calendar.py
git commit -m "feat(cli-calendar): wire economic find dispatch"
```

---

## Task 10: Wire `earnings upcoming` and `earnings past` dispatch

**Files:**
- Modify: `src/trading_agent_skills/cli/calendar.py`
- Modify: `tests/test_cli_calendar.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_cli_calendar.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```
.venv/bin/python -m pytest tests/test_cli_calendar.py -v
```

Expected: FAIL with `NotImplementedError`.

- [ ] **Step 3: Add the dispatch branches**

In `src/trading_agent_skills/cli/calendar.py`, add inside `run()`'s `try` block (after the `economic find` branch):

```python
        if args.noun == "earnings" and args.verb == "upcoming":
            resp = client.fetch_earnings(
                symbols=args.symbols,
                limit=args.limit,
            )
            if args.raw:
                return _emit(resp.payload)
            enriched = enrich_events(resp.payload, now_utc=now)
            enriched = _filter_within_days(enriched, args.within_days, now)
            return _emit(enriched)

        if args.noun == "earnings" and args.verb == "past":
            resp = client.fetch_earnings_past(
                symbols=args.symbols,
                limit=args.limit,
            )
            if args.raw:
                return _emit(resp.payload)
            enriched = enrich_events(resp.payload, now_utc=now)
            return _emit(enriched)
```

Now remove the trailing `raise NotImplementedError(...)` line — all branches are wired.

- [ ] **Step 4: Run tests to verify pass**

```
.venv/bin/python -m pytest tests/test_cli_calendar.py -q
.venv/bin/python -m pytest tests/ -q
```

Expected: PASS for everything.

- [ ] **Step 5: Commit**

```bash
git add src/trading_agent_skills/cli/calendar.py tests/test_cli_calendar.py
git commit -m "feat(cli-calendar): wire earnings upcoming + past dispatch"
```

---

## Task 11: Failure-mode coverage

**Files:**
- Modify: `tests/test_cli_calendar.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_cli_calendar.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail or pass**

```
.venv/bin/python -m pytest tests/test_cli_calendar.py -v
```

Expected: All three should already PASS — the dispatch already catches `CalixUnavailable` and `enrich_events` already sets `degraded`. If any fail, fix the dispatch in `cli/calendar.py` (likely needs the `try/except` to encompass all branches — verify the `try:` is at the top of the dispatch and `except CalixUnavailable as exc: return _emit_calix_error(exc)` is at the bottom).

- [ ] **Step 3: Run full test suite**

```
.venv/bin/python -m pytest tests/ -q
```

Expected: 600+ tests PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_cli_calendar.py
git commit -m "test(cli-calendar): cover Calix 5xx, network failure, and stale payload"
```

---

## Task 12: Write the SKILL.md

**Files:**
- Create: `.claude/skills/calendar/SKILL.md`

- [ ] **Step 1: Create the file**

Create `.claude/skills/calendar/SKILL.md`:

````markdown
---
name: calendar
description: Use BEFORE mentioning ANY economic event or earnings release — past or future. Covers (a) upcoming catalysts ("when is the next CPI", "any catalysts tonight"), (b) past prints ("what did CPI come in at", "did NFP beat", "PPI actual"), and (c) specific event lookups ("CPI on May 12"). Read-only — never invent event timing or values from training data; if this skill returns nothing, say nothing.
---

# Calendar

Looks up upcoming and past economic calendar events (CPI, NFP, FOMC, GDP, PMI, central-bank decisions) and corporate earnings releases via the Calix API. Returns structured JSON with computed time fields (`minutes_until` / `minutes_since` / `local_time_aest`) so you don't have to do timezone math.

This skill is the single source of truth for "what's on the calendar" and "what did X print at." Never answer either question from training data.

## Hard rules

1. **Future events** — never reference an upcoming event without invoking `economic upcoming` or `earnings upcoming` in the same turn. The data has a release schedule that changes weekly; your training data does not.
2. **Past values** — never quote a `forecast`, `previous`, or `actual` value without invoking `economic past` / `earnings past` / `economic find` and reading the value directly from JSON output. Confabulating "CPI printed 3.8%" is the same failure mode as confabulating "CPI tonight" — both are bullshit-from-training-data.
3. **No partial citation** — if the skill returns an event with `actual: null`, the answer is "the print hasn't been released yet" or "the data source doesn't have the actual value." NOT a guess based on consensus or news.
4. **Disambiguate when find returns multiple matches** — if `find` returns 3 different "CPI" events for the same date (CPI m/m, CPI y/y, Core CPI m/m), present all three with their titles. Never pick one and pretend it's the canonical answer.
5. **Trust the staleness flag** — if the response includes `degraded: true`, caveat your answer with "calendar feed flagged stale (>3h since refresh)".

## Prerequisites

The CLI is `trading-agent-skills-calendar`. Verify with:

```bash
trading-agent-skills-calendar --help
```

If not found: install the Python package — from the agent-trading-skills repo, run `pip install -e .` in a venv your harness can see.

The Calix endpoint is `https://calix.fintrixmarkets.com`. No auth required; results are edge-cached for 5 minutes.

## When to invoke

**Upcoming:**
- "is there a CPI tonight" / "when's the next NFP"
- "what's coming up this week" / "any catalysts today"
- "should I be careful around news today"

**Past:**
- "what did CPI come in at" / "did NFP beat"
- "PPI actual" / "MSFT earnings beat?"
- "what was the print on the May 12 CPI"

**Specific lookup (use `find`):**
- "CPI on May 12" / "NFP last Friday" / "AAPL Q1 EPS"

**Indirect:** when a user is discussing a trade and you're about to mention upcoming or past macro events as part of the analysis, invoke first. E.g.:

> User: "I'm thinking about going long NAS100 — anything coming up?"

→ Call `economic upcoming --currencies USD --within-hours 48` BEFORE answering.

## CLI surface

```
trading-agent-skills-calendar economic upcoming  [--currencies USD,EUR | majors | all] [--impact High,Medium,Low,Holiday] [--limit N] [--within-hours H] [--raw]
trading-agent-skills-calendar economic past      [--currencies …] [--impact …] [--limit N] [--within-hours H] [--raw]
trading-agent-skills-calendar economic find      --title SUBSTR [--currency CODE] [--date YYYY-MM-DD] [--days-back N=7] [--raw]

trading-agent-skills-calendar earnings upcoming  [--symbols AAPL,MSFT] [--limit N] [--within-days D] [--raw]
trading-agent-skills-calendar earnings past      [--symbols …] [--limit N] [--within-days D] [--raw]
```

Defaults: `--currencies majors` (USD/EUR/GBP/JPY/AUD/CAD/CHF/NZD), `--impact High`, `--limit 10`. Calix caps `--limit` at 25.

## Output shape

**Economic upcoming/past:**
```json
{
  "updatedAt": "2026-05-14T02:01:33Z",
  "source": "tradays",
  "stale": false,
  "degraded": false,
  "fetched_at_utc": "2026-05-14T02:05:00+00:00",
  "now_utc": "2026-05-14T02:05:00+00:00",
  "events": [
    {
      "id": "ff_usd_cpi_y_y_20260512",
      "title": "CPI y/y",
      "currency": "USD",
      "impact": "High",
      "scheduledAt": "2026-05-12T12:30:00.000Z",
      "forecast": "3.7%",
      "previous": "3.3%",
      "actual": "3.8%",
      "minutes_until": -2375,
      "minutes_since": 2375,
      "is_past": true,
      "local_time_aest": "2026-05-12 22:30 AEST",
      "actual_present": true
    }
  ]
}
```

**Economic find:**
```json
{
  "query": {"title": "CPI", "currency": "USD", "date": "2026-05-12"},
  "matches": [<events>],
  "near_misses": [<events with same title+currency but different date>],
  "fetched_at_utc": "...",
  "source": "tradays",
  "stale": false
}
```

**Earnings:** same shape, with `earnings[]` instead of `events[]`, `days_until`/`days_since` instead of `minutes_*`, and `epsEstimate`/`epsActual`/`revenueEstimate`/`revenueActual` fields populated on past entries.

## Handling responses

| Output | What it means | What you say |
|---|---|---|
| `events: [<...>]` (or `earnings`) | Hits found. | Quote `title`, `actual`/`forecast`/`previous`, `local_time_aest` directly from the JSON. |
| `events: []` | No qualifying events. | "No qualifying events in that window." Do NOT invent. |
| `degraded: true` | Calix's cache is >3h stale. | Caveat: "calendar feed flagged stale". |
| Exit code 2 + `{error, source: "calix"}` | Calix unreachable / 5xx / network error. | "Calendar source unreachable — can't verify; not making any claims about events right now." |

## Workflow

1. **Pick the right subcommand** based on the question (upcoming / past / find).
2. **Tighten the filter** — if the user asked about a specific currency or symbol, use `--currencies` / `--symbols`. If the question is time-bounded, use `--within-hours` / `--within-days`. Tight filters mean fewer hallucination opportunities.
3. **Run the CLI** and read the JSON output.
4. **Apply the rules above** when shaping your answer.
5. **For `find` with multiple matches** — list every match. Do not collapse to one number.
````

- [ ] **Step 2: Verify the file looks right**

```bash
cat .claude/skills/calendar/SKILL.md | head -10
```

Expected: shows the YAML frontmatter with `name: calendar`.

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/calendar/SKILL.md
git commit -m "feat(skill): add calendar SKILL.md with hard rules + workflow"
```

---

## Task 13: CHANGELOG entry + final test sweep

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Read the current CHANGELOG**

```bash
head -30 CHANGELOG.md
```

- [ ] **Step 2: Add an "Unreleased" section**

If a `## Unreleased` section already exists at the top, append the new bullet under it. Otherwise, insert immediately under the `# Changelog` title:

```markdown
## Unreleased

### Added
- `trading-agent-skills-calendar` CLI with `economic upcoming|past|find` and `earnings upcoming|past` subcommands. Wraps the existing `CalixClient`, adds time enrichment (`minutes_until`, `minutes_since`, `local_time_aest`), and exposes `find_events` for "what did X print at" lookups.
- `.claude/skills/calendar/SKILL.md` — first-class calendar skill so any agent can ground event-timing and event-value claims in Calix data instead of confabulating from training data.
- `CalixClient.fetch_economic_past()` and `CalixClient.fetch_earnings_past()`. Earnings methods now accept an optional `symbols` filter.
```

- [ ] **Step 3: Run full test suite one more time**

```bash
.venv/bin/python -m pytest tests/ -q
```

Expected: ALL tests PASS (existing 590+ plus ~22 new = ~612+).

- [ ] **Step 4: Smoke-test against live Calix (optional but recommended)**

```bash
trading-agent-skills-calendar economic past --currencies USD --impact High --limit 5
```

Expected: JSON with recent USD high-impact events including CPI/PPI actuals.

```bash
trading-agent-skills-calendar economic find --title CPI --currency USD --date 2026-05-12
```

Expected: JSON with `matches` containing CPI m/m, CPI y/y, Core CPI m/m, etc.

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): note calendar CLI + skill in Unreleased"
```

---

## Done — implementation complete

Branch is `feat/calendar-skill-spec` (already created off v0.3.0 with the spec doc commit). After merging this branch, the downstream wiring tracked in section 8 of the spec (morning-brief edits, MM SOUL hard rule, etc.) lives in `~/.openclaw/workspace`, NOT in this repo, and is a separate session.
