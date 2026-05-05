"""AlphaVantage NEWS_SENTIMENT enrichment for the news brief pipeline.

Matches AV sentiment entries to existing ``NewsArticle`` objects gathered
from Finnhub / Marketaux / ForexNews by canonical URL first, then headline
similarity. Matched articles receive ``sentiment_score``, ``sentiment_label``,
and ``relevance_score`` fields. Unmatched AV entries are converted to new
``NewsArticle`` items — effectively making AlphaVantage a 4th news source.

This module is pure — no I/O, no network calls.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from trading_agent_skills.news_dedup import (
    NewsArticle,
    canonicalise_url,
    classify_impact,
    levenshtein_ratio,
)


_TITLE_SIMILARITY_THRESHOLD = 0.85


def _match_av_to_article(
    av_entry: dict[str, Any],
    articles: list[NewsArticle],
) -> int | None:
    av_url = canonicalise_url(av_entry.get("url", ""))
    av_title = av_entry.get("title", "")

    if av_url:
        for i, article in enumerate(articles):
            if article.canonical_url and article.canonical_url == av_url:
                return i

    if av_title:
        best_idx = None
        best_ratio = 0.0
        for i, article in enumerate(articles):
            ratio = levenshtein_ratio(av_title, article.title)
            if ratio >= _TITLE_SIMILARITY_THRESHOLD and ratio > best_ratio:
                best_ratio = ratio
                best_idx = i
        return best_idx

    return None


def _extract_ticker_sentiment(
    av_entry: dict[str, Any],
) -> tuple[float | None, str | None, float | None]:
    ticker_sentiments = av_entry.get("ticker_sentiment", [])
    if ticker_sentiments:
        ts = ticker_sentiments[0]
        return (
            float(ts["ticker_sentiment_score"]),
            ts.get("ticker_sentiment_label"),
            float(ts["relevance_score"]) if "relevance_score" in ts else None,
        )
    score = av_entry.get("overall_sentiment_score")
    label = av_entry.get("overall_sentiment_label")
    if score is not None:
        return float(score), label, None
    return None, None, None


def _av_entry_to_article(av_entry: dict[str, Any]) -> NewsArticle:
    url = av_entry.get("url", "")
    title = av_entry.get("title", "")
    summary = av_entry.get("summary", "")
    raw_time = av_entry.get("time_published", "")
    if raw_time:
        try:
            published = datetime.strptime(raw_time, "%Y%m%dT%H%M%S").replace(
                tzinfo=timezone.utc,
            )
        except ValueError:
            published = datetime.now(timezone.utc)
    else:
        published = datetime.now(timezone.utc)

    symbols = tuple(
        ts.get("ticker", "") for ts in av_entry.get("ticker_sentiment", [])
    )
    score, label, relevance = _extract_ticker_sentiment(av_entry)
    return NewsArticle(
        title=title,
        summary=summary[:300],
        url=url,
        canonical_url=canonicalise_url(url),
        published_at_utc=published,
        source="alphavantage",
        publisher=av_entry.get("source", ""),
        symbols=symbols,
        keywords=(),
        impact=classify_impact(title, summary),
        sentiment_score=score,
        sentiment_label=label,
        relevance_score=relevance,
    )


def enrich_articles_with_sentiment(
    articles: list[NewsArticle],
    av_sentiment: list[dict[str, Any]],
) -> list[NewsArticle]:
    if not av_sentiment:
        return list(articles)
    result = list(articles)
    new_articles: list[NewsArticle] = []

    for av_entry in av_sentiment:
        idx = _match_av_to_article(av_entry, result)
        if idx is not None:
            score, label, relevance = _extract_ticker_sentiment(av_entry)
            result[idx] = replace(
                result[idx],
                sentiment_score=score,
                sentiment_label=label,
                relevance_score=relevance,
            )
        else:
            new_articles.append(_av_entry_to_article(av_entry))

    result.extend(new_articles)
    return result


__all__ = ["enrich_articles_with_sentiment"]
