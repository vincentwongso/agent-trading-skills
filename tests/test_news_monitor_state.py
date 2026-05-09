"""Tests for news_seen.jsonl state file helpers."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from trading_agent_skills.news_monitor import (
    StateEntry,
    compute_event_id,
    load_state,
    write_state,
)


def test_event_id_stable_for_identical_inputs() -> None:
    a = compute_event_id("https://example.com/fed-holds", "Fed holds rates")
    b = compute_event_id("https://example.com/fed-holds", "Fed holds rates")
    assert a == b
    assert len(a) == 16


def test_event_id_normalises_headline_whitespace() -> None:
    a = compute_event_id("https://example.com/x", "Fed holds rates")
    b = compute_event_id("https://example.com/x", "  Fed   Holds Rates  ")
    assert a == b


def test_event_id_differs_for_different_urls() -> None:
    a = compute_event_id("https://example.com/x", "h")
    b = compute_event_id("https://example.com/y", "h")
    assert a != b


def test_load_state_missing_file_returns_empty(tmp_path: Path) -> None:
    state = load_state(tmp_path / "missing.jsonl", ttl_hours=24,
                       now=datetime(2026, 5, 9, tzinfo=timezone.utc))
    assert state == set()


def test_load_state_filters_expired_entries(tmp_path: Path) -> None:
    p = tmp_path / "news_seen.jsonl"
    now = datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc)
    fresh = (now - timedelta(hours=2)).isoformat()
    stale = (now - timedelta(hours=30)).isoformat()
    p.write_text(
        "\n".join([
            json.dumps({"event_id": "fresh1", "first_seen_utc": fresh}),
            json.dumps({"event_id": "stale1", "first_seen_utc": stale}),
            json.dumps({"event_id": "fresh2", "first_seen_utc": fresh}),
        ]),
        encoding="utf-8",
    )
    state = load_state(p, ttl_hours=24, now=now)
    assert state == {"fresh1", "fresh2"}


def test_load_state_ignores_corrupt_lines(tmp_path: Path) -> None:
    p = tmp_path / "news_seen.jsonl"
    now = datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc)
    fresh = (now - timedelta(hours=1)).isoformat()
    p.write_text(
        "\n".join([
            json.dumps({"event_id": "ok", "first_seen_utc": fresh}),
            "not json",
            json.dumps({"missing_event_id": True, "first_seen_utc": fresh}),
        ]),
        encoding="utf-8",
    )
    state = load_state(p, ttl_hours=24, now=now)
    assert state == {"ok"}


def test_write_state_persists_only_fresh_entries(tmp_path: Path) -> None:
    p = tmp_path / "news_seen.jsonl"
    now = datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc)
    new_entries = [
        StateEntry(event_id="a", first_seen_utc=now),
        StateEntry(event_id="b", first_seen_utc=now),
    ]
    write_state(p, ttl_hours=24, now=now, existing=set(), new_entries=new_entries)
    lines = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()]
    assert {l["event_id"] for l in lines} == {"a", "b"}


def test_write_state_appends_to_existing(tmp_path: Path) -> None:
    p = tmp_path / "news_seen.jsonl"
    now = datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc)
    fresh = (now - timedelta(hours=1)).isoformat()
    p.write_text(json.dumps({"event_id": "old", "first_seen_utc": fresh}) + "\n",
                 encoding="utf-8")
    write_state(p, ttl_hours=24, now=now, existing={"old"},
                new_entries=[StateEntry(event_id="new", first_seen_utc=now)])
    ids = {json.loads(l)["event_id"] for l in p.read_text(encoding="utf-8").splitlines()}
    assert ids == {"old", "new"}


def test_write_state_drops_expired_old_entries_on_rotation(tmp_path: Path) -> None:
    p = tmp_path / "news_seen.jsonl"
    now = datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc)
    stale_iso = (now - timedelta(hours=30)).isoformat()
    p.write_text(json.dumps({"event_id": "ancient", "first_seen_utc": stale_iso}) + "\n",
                 encoding="utf-8")
    write_state(p, ttl_hours=24, now=now, existing=set(),
                new_entries=[StateEntry(event_id="new", first_seen_utc=now)])
    ids = {json.loads(l)["event_id"] for l in p.read_text(encoding="utf-8").splitlines()}
    assert ids == {"new"}  # ancient dropped on rotation
