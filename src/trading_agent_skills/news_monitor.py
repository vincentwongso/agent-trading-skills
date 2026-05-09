"""News monitor — fetch fresh high-impact articles from upstream news clients,
classify severity, dedup against persistent state, emit push events.

This module is the engine. The CLI wrapper lives in
``trading_agent_skills.cli.news_monitor`` and the integrating MM bridge lives
downstream (out-of-tree) in ``trader_cli.py``.

Severity gate combines two signals:
  * keyword classifier (``news_dedup.classify_impact``) — deterministic, free
  * AlphaVantage quantitative sentiment + relevance — optional (gated on
    ``ALPHAVANTAGE_API_KEY``); catches market-moving stories the keyword list
    misses.

A PUSH-grade event is one that satisfies either signal independently. Events
that satisfy both are tagged ``severity_reason="both"`` for telemetry.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from trading_agent_skills.news_dedup import NewsArticle


@dataclass(frozen=True)
class SeverityThresholds:
    abs_sentiment: float = 0.35
    relevance: float = 0.5


def severity_decision(
    article: NewsArticle,
    thresholds: SeverityThresholds,
) -> tuple[bool, str]:
    """Return (is_push_grade, reason).

    Reason is one of: "keyword" / "sentiment" / "both" / "" (when not push).
    """
    keyword_high = article.impact == "high"
    sentiment_high = (
        article.sentiment_score is not None
        and article.relevance_score is not None
        and abs(article.sentiment_score) >= thresholds.abs_sentiment
        and article.relevance_score >= thresholds.relevance
    )
    if keyword_high and sentiment_high:
        return True, "both"
    if keyword_high:
        return True, "keyword"
    if sentiment_high:
        return True, "sentiment"
    return False, ""


# ---------- Event ID + state file ------------------------------------------


_HEADLINE_NORMALISE = re.compile(r"\s+")


def compute_event_id(canonical_url: str, headline: str) -> str:
    """Stable 16-char hex ID for cross-tick dedup.

    Combines canonicalised URL with whitespace-normalised lowercase headline.
    Two articles with the same URL or near-identical headline collapse.
    """
    norm_headline = _HEADLINE_NORMALISE.sub(" ", headline.strip().lower())
    payload = f"{canonical_url}|{norm_headline}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


@dataclass(frozen=True)
class StateEntry:
    event_id: str
    first_seen_utc: datetime


def load_state(
    path: Path,
    *,
    ttl_hours: int,
    now: datetime,
) -> set[str]:
    """Read news_seen.jsonl, drop entries older than ttl_hours, return event_ids."""
    if not path.exists():
        return set()
    cutoff = now - timedelta(hours=ttl_hours)
    fresh: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            blob = json.loads(line)
        except json.JSONDecodeError:
            continue
        eid = blob.get("event_id")
        ts_raw = blob.get("first_seen_utc")
        if not isinstance(eid, str) or not isinstance(ts_raw, str):
            continue
        try:
            ts = datetime.fromisoformat(ts_raw)
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts >= cutoff:
            fresh.add(eid)
    return fresh


def write_state(
    path: Path,
    *,
    ttl_hours: int,
    now: datetime,
    existing: set[str],
    new_entries: Iterable[StateEntry],
) -> None:
    """Atomically rewrite news_seen.jsonl with fresh existing + new entries.

    Existing entries are preserved by re-reading the file (filtered by ttl_hours)
    so this function is safe under concurrent monitor runs (last writer wins;
    PK collision is caught downstream by the bridge's UNIQUE constraint).
    """
    cutoff = now - timedelta(hours=ttl_hours)
    rows: list[dict] = []

    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                blob = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts_raw = blob.get("first_seen_utc")
            try:
                ts = datetime.fromisoformat(str(ts_raw))
            except (TypeError, ValueError):
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= cutoff:
                rows.append({"event_id": blob["event_id"],
                             "first_seen_utc": ts.isoformat()})

    seen_ids = {r["event_id"] for r in rows}
    for entry in new_entries:
        if entry.event_id in seen_ids:
            continue
        rows.append({
            "event_id": entry.event_id,
            "first_seen_utc": entry.first_seen_utc.isoformat(),
        })
        seen_ids.add(entry.event_id)

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n" if rows else "",
        encoding="utf-8",
    )
    os.replace(tmp, path)


__all__ = [
    "SeverityThresholds",
    "severity_decision",
    "StateEntry",
    "compute_event_id",
    "load_state",
    "write_state",
]
