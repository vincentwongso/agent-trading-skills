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


# ---------- Orchestrator ---------------------------------------------------


@dataclass(frozen=True)
class PushEvent:
    event_id: str
    headline: str
    summary: str
    sources: tuple[str, ...]
    symbols_implicated: tuple[str, ...]
    impact: str  # "high"
    sentiment_score: float | None
    relevance_score: float | None
    urls: tuple[str, ...]
    published_at_utc: datetime
    severity_reason: str  # "keyword" / "sentiment" / "both"


@dataclass
class NewsMonitorInput:
    now_utc: datetime
    lookback_minutes: int
    state_path: Path
    state_ttl_hours: int
    thresholds: SeverityThresholds
    clients: dict  # {"finnhub": FinnhubClient, ...}


@dataclass(frozen=True)
class NewsMonitorResult:
    events: list[PushEvent]
    provider_health: dict[str, str]
    flags: list[str]


_DEFAULT_AV_TOPICS = ("economy_macro", "financial_markets", "energy_transportation")
_DEFAULT_MARKETAUX_SYMBOLS = ()  # MM watchlist not piped in; use general-news mode
_DEFAULT_FOREXNEWS_PAIRS = ("EUR-USD", "XAU-USD", "GBP-USD")


def monitor(inp: NewsMonitorInput) -> NewsMonitorResult:
    """Fetch fresh news, classify, dedup, return new push-grade events."""
    from trading_agent_skills.news_dedup import dedupe_articles

    lookback_hours = max(1, (inp.lookback_minutes + 59) // 60)

    health: dict[str, str] = {}
    articles_by_provider: dict[str, list[NewsArticle]] = {}

    fc = inp.clients.get("finnhub")
    if fc is not None:
        arts, status = fc.fetch_general(lookback_hours=lookback_hours)
        articles_by_provider["finnhub"] = list(arts)
        health["finnhub"] = status

    mc = inp.clients.get("marketaux")
    if mc is not None:
        arts, status = mc.fetch(symbols=_DEFAULT_MARKETAUX_SYMBOLS,
                                lookback_hours=lookback_hours)
        articles_by_provider["marketaux"] = list(arts)
        health["marketaux"] = status

    fnc = inp.clients.get("forexnews")
    if fnc is not None:
        arts, status = fnc.fetch(currencypairs=_DEFAULT_FOREXNEWS_PAIRS)
        articles_by_provider["forexnews"] = list(arts)
        health["forexnews"] = status

    av = inp.clients.get("alphavantage")
    if av is not None:
        arts, status = av.fetch(topics=_DEFAULT_AV_TOPICS,
                                lookback_hours=lookback_hours)
        articles_by_provider["alphavantage"] = list(arts)
        health["alphavantage"] = status

    flags: list[str] = []
    healthy = [s for s in health.values() if s in ("ok", "cache")]
    if health and not healthy:
        flags.append("NEWS_PROVIDER_ALL_DEGRADED")
        return NewsMonitorResult(events=[], provider_health=health, flags=flags)

    flat: list[NewsArticle] = []
    for arts in articles_by_provider.values():
        flat.extend(arts)
    if not flat:
        return NewsMonitorResult(events=[], provider_health=health, flags=flags)

    clusters = dedupe_articles(flat)
    seen = load_state(inp.state_path, ttl_hours=inp.state_ttl_hours, now=inp.now_utc)

    events: list[PushEvent] = []
    new_entries: list[StateEntry] = []
    for cluster in clusters:
        primary = cluster.primary
        is_push, reason = severity_decision(primary, inp.thresholds)
        if not is_push:
            continue
        eid = compute_event_id(primary.canonical_url, primary.title)
        if eid in seen:
            continue
        sources = cluster.all_sources
        symbols = tuple(dict.fromkeys(
            s for art in cluster.all_articles for s in art.symbols
        ))
        urls = tuple(dict.fromkeys(
            art.url for art in cluster.all_articles if art.url
        ))
        events.append(PushEvent(
            event_id=eid,
            headline=primary.title,
            summary=primary.summary,
            sources=sources,
            symbols_implicated=symbols,
            impact="high",  # severity gate guarantees push-grade
            sentiment_score=primary.sentiment_score,
            relevance_score=primary.relevance_score,
            urls=urls,
            published_at_utc=primary.published_at_utc,
            severity_reason=reason,
        ))
        new_entries.append(StateEntry(event_id=eid, first_seen_utc=inp.now_utc))

    if new_entries:
        write_state(
            inp.state_path,
            ttl_hours=inp.state_ttl_hours,
            now=inp.now_utc,
            existing=seen,
            new_entries=new_entries,
        )

    return NewsMonitorResult(events=events, provider_health=health, flags=flags)


__all__ = [
    "SeverityThresholds",
    "severity_decision",
    "StateEntry",
    "compute_event_id",
    "load_state",
    "write_state",
    "PushEvent",
    "NewsMonitorInput",
    "NewsMonitorResult",
    "monitor",
]
