"""Tests for news-monitor severity gate."""

from __future__ import annotations

from datetime import datetime, timezone

from trading_agent_skills.news_dedup import NewsArticle
from trading_agent_skills.news_monitor import SeverityThresholds, severity_decision


def _article(
    *,
    title: str = "FOMC holds rates",
    summary: str = "",
    impact: str = "high",
    sentiment_score: float | None = None,
    relevance_score: float | None = None,
) -> NewsArticle:
    return NewsArticle(
        title=title,
        summary=summary,
        url="https://example.com/x",
        canonical_url="https://example.com/x",
        published_at_utc=datetime(2026, 5, 9, 1, 0, tzinfo=timezone.utc),
        source="finnhub",
        publisher="Reuters",
        symbols=("USD",),
        keywords=(),
        impact=impact,
        sentiment_score=sentiment_score,
        relevance_score=relevance_score,
    )


def test_keyword_high_passes() -> None:
    a = _article(title="FOMC holds rates", impact="high",
                 sentiment_score=None, relevance_score=None)
    push, reason = severity_decision(a, SeverityThresholds())
    assert push is True
    assert reason == "keyword"


def test_low_with_strong_sentiment_passes() -> None:
    a = _article(title="surprise op-ed roils markets", impact="low",
                 sentiment_score=-0.50, relevance_score=0.70)
    push, reason = severity_decision(a, SeverityThresholds())
    assert push is True
    assert reason == "sentiment"


def test_keyword_high_and_sentiment_passes_both() -> None:
    a = _article(impact="high", sentiment_score=-0.50, relevance_score=0.70)
    push, reason = severity_decision(a, SeverityThresholds())
    assert push is True
    assert reason == "both"


def test_low_with_weak_sentiment_skips() -> None:
    a = _article(impact="low", sentiment_score=-0.20, relevance_score=0.70)
    push, _ = severity_decision(a, SeverityThresholds())
    assert push is False


def test_low_with_strong_sentiment_but_low_relevance_skips() -> None:
    a = _article(impact="low", sentiment_score=-0.50, relevance_score=0.30)
    push, _ = severity_decision(a, SeverityThresholds())
    assert push is False


def test_low_with_no_sentiment_skips() -> None:
    a = _article(impact="low", sentiment_score=None, relevance_score=None)
    push, _ = severity_decision(a, SeverityThresholds())
    assert push is False


def test_thresholds_overridable() -> None:
    a = _article(impact="low", sentiment_score=-0.30, relevance_score=0.40)
    push, reason = severity_decision(
        a, SeverityThresholds(abs_sentiment=0.25, relevance=0.35),
    )
    assert push is True
    assert reason == "sentiment"
