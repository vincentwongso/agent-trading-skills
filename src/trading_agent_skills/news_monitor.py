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

from dataclasses import dataclass

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


__all__ = ["SeverityThresholds", "severity_decision"]
