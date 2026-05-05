"""Tests for ``trading_agent_skills.av_sentiment``."""

from __future__ import annotations

from datetime import datetime, timezone

from trading_agent_skills.av_sentiment import (
    enrich_articles_with_sentiment,
)
from trading_agent_skills.news_dedup import NewsArticle, canonicalise_url


def _article(
    *,
    title: str,
    url: str = "https://reuters.com/x",
    publisher: str = "Reuters",
    source: str = "finnhub",
    published: datetime | None = None,
    symbols: tuple[str, ...] = (),
) -> NewsArticle:
    return NewsArticle(
        title=title,
        summary="",
        url=url,
        canonical_url=canonicalise_url(url),
        published_at_utc=published or datetime(2026, 5, 5, 12, 0, tzinfo=timezone.utc),
        source=source,
        publisher=publisher,
        symbols=symbols,
        keywords=(),
        impact="low",
    )


def _av_entry(
    *,
    title: str = "Gold rises on safe-haven demand",
    url: str = "https://reuters.com/x",
    time_published: str = "20260505T120000",
    source: str = "Reuters",
    overall_sentiment_score: float = 0.25,
    overall_sentiment_label: str = "Somewhat-Bullish",
    ticker_sentiment: list[dict] | None = None,
) -> dict:
    return {
        "title": title,
        "url": url,
        "time_published": time_published,
        "source": source,
        "summary": "Gold prices rose...",
        "overall_sentiment_score": overall_sentiment_score,
        "overall_sentiment_label": overall_sentiment_label,
        "ticker_sentiment": ticker_sentiment or [],
    }


def test_match_by_url() -> None:
    articles = [_article(title="Gold rises", url="https://reuters.com/gold")]
    av = [_av_entry(url="https://reuters.com/gold", overall_sentiment_score=0.5)]
    result = enrich_articles_with_sentiment(articles, av)
    assert len(result) == 1
    assert result[0].sentiment_score == 0.5
    assert result[0].title == "Gold rises"


def test_match_by_url_canonical() -> None:
    articles = [_article(title="Gold rises", url="https://reuters.com/gold")]
    av = [_av_entry(url="https://reuters.com/gold?utm_source=twitter")]
    result = enrich_articles_with_sentiment(articles, av)
    assert len(result) == 1
    assert result[0].sentiment_score is not None


def test_match_by_title_similarity() -> None:
    articles = [_article(title="Gold rises on safe-haven demand", url="https://reuters.com/a")]
    av = [_av_entry(title="Gold Rises On Safe-Haven Demand", url="https://cnbc.com/b")]
    result = enrich_articles_with_sentiment(articles, av)
    assert len(result) == 1
    assert result[0].sentiment_score is not None


def test_no_match_different_title() -> None:
    articles = [_article(title="Oil falls on weak demand")]
    av = [_av_entry(title="Fed holds rates steady", url="https://cnbc.com/fed")]
    result = enrich_articles_with_sentiment(articles, av)
    assert len(result) == 2
    new = result[1]
    assert new.source == "alphavantage"
    assert new.title == "Fed holds rates steady"


def test_ticker_sentiment_used_when_available() -> None:
    articles = [_article(title="AAPL earnings beat", symbols=("AAPL",))]
    av = [_av_entry(
        title="AAPL earnings beat",
        overall_sentiment_score=0.1,
        ticker_sentiment=[
            {"ticker": "AAPL", "ticker_sentiment_score": "0.75",
             "ticker_sentiment_label": "Bullish", "relevance_score": "0.9"},
        ],
    )]
    result = enrich_articles_with_sentiment(articles, av)
    assert result[0].sentiment_score == 0.75
    assert result[0].sentiment_label == "Bullish"
    assert result[0].relevance_score == 0.9


def test_unmatched_creates_article_with_alphavantage_source() -> None:
    av = [_av_entry(
        title="Breaking: markets crash",
        url="https://cnbc.com/crash",
        time_published="20260505T143000",
    )]
    result = enrich_articles_with_sentiment([], av)
    assert len(result) == 1
    art = result[0]
    assert art.source == "alphavantage"
    assert art.title == "Breaking: markets crash"
    assert art.published_at_utc == datetime(2026, 5, 5, 14, 30, tzinfo=timezone.utc)


def test_unmatched_time_published_parsing() -> None:
    av = [_av_entry(time_published="20260101T093015", url="https://x.com/1")]
    result = enrich_articles_with_sentiment([], av)
    assert result[0].published_at_utc == datetime(2026, 1, 1, 9, 30, 15, tzinfo=timezone.utc)


def test_original_list_not_mutated() -> None:
    articles = [_article(title="Gold rises", url="https://reuters.com/gold")]
    av = [_av_entry(url="https://reuters.com/gold")]
    original_len = len(articles)
    result = enrich_articles_with_sentiment(articles, av)
    assert len(articles) == original_len
    assert articles[0].sentiment_score is None


def test_empty_articles_empty_av() -> None:
    assert enrich_articles_with_sentiment([], []) == []


def test_empty_av_returns_articles_unchanged() -> None:
    articles = [_article(title="Gold rises")]
    result = enrich_articles_with_sentiment(articles, [])
    assert len(result) == 1
    assert result[0].sentiment_score is None
