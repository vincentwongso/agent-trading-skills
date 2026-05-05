"""Tests for AlphaVantage integration in ``trading_agent_skills.news_brief``."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from trading_agent_skills.macro_context import MacroContext, MacroReading
from trading_agent_skills.news_brief import NewsBriefInput, build
from trading_agent_skills.news_dedup import NewsArticle, canonicalise_url
from trading_agent_skills.watchlist import resolve_watchlist


def _input(
    *,
    macro_context: MacroContext | None = None,
    top_movers: dict | None = None,
) -> NewsBriefInput:
    res = resolve_watchlist(explicit=("XAUUSD",), default=("XAUUSD",))
    return NewsBriefInput(
        now_utc=datetime(2026, 5, 5, 21, 0, tzinfo=timezone.utc),
        lookahead_hours=4,
        lookback_hours=12,
        watchlist=res,
        bars_by_symbol={},
        symbol_meta={},
        economic_events=[],
        earnings_entries=[],
        economic_stale=False,
        earnings_stale=False,
        articles_by_provider={},
        provider_status={"finnhub": "ok"},
        macro_context=macro_context,
        top_movers=top_movers,
    )


def test_build_includes_macro_context_when_present() -> None:
    ctx = MacroContext(
        readings=(
            MacroReading(
                name="CPI",
                latest_value=Decimal("315.0"),
                latest_date="2026-04-01",
                previous_value=Decimal("312.5"),
                previous_date="2026-03-01",
                direction="rising",
            ),
        ),
        staleness_flags=(),
    )
    result = build(_input(macro_context=ctx))
    assert result.macro_context is not None
    assert result.macro_context.readings[0].name == "CPI"
    assert result.macro_context.readings[0].direction == "rising"


def test_build_omits_macro_context_when_absent() -> None:
    result = build(_input())
    assert result.macro_context is None


def test_macro_staleness_surfaces_flag() -> None:
    ctx = MacroContext(
        readings=(),
        staleness_flags=("REAL_GDP",),
    )
    result = build(_input(macro_context=ctx))
    assert "AV_MACRO_STALE" in result.flags


def test_macro_no_staleness_no_flag() -> None:
    ctx = MacroContext(readings=(), staleness_flags=())
    result = build(_input(macro_context=ctx))
    assert "AV_MACRO_STALE" not in result.flags


def test_build_includes_top_movers_when_present() -> None:
    movers = {
        "top_gainers": [{"ticker": "AAPL", "change_percentage": "5.2%"}],
        "top_losers": [{"ticker": "TSLA", "change_percentage": "-3.1%"}],
        "most_actively_traded": [],
    }
    result = build(_input(top_movers=movers))
    assert result.top_movers is not None
    assert result.top_movers["top_gainers"][0]["ticker"] == "AAPL"


def test_build_omits_top_movers_when_absent() -> None:
    result = build(_input())
    assert result.top_movers is None


def test_news_item_carries_sentiment_from_article() -> None:
    from trading_agent_skills.news_brief import NewsItem
    from trading_agent_skills.news_dedup import ClusteredArticle

    article = NewsArticle(
        title="Gold rises",
        summary="Gold prices...",
        url="https://reuters.com/gold",
        canonical_url="https://reuters.com/gold",
        published_at_utc=datetime(2026, 5, 5, 12, 0, tzinfo=timezone.utc),
        source="finnhub",
        publisher="Reuters",
        symbols=("XAUUSD",),
        keywords=(),
        impact="medium",
        sentiment_score=0.65,
        sentiment_label="Bullish",
        relevance_score=0.9,
    )
    cluster = ClusteredArticle(primary=article)
    item = NewsItem.from_cluster(cluster)
    assert item.sentiment_score == 0.65
    assert item.sentiment_label == "Bullish"
    assert item.relevance_score == 0.9


def test_news_item_none_sentiment_when_absent() -> None:
    from trading_agent_skills.news_brief import NewsItem
    from trading_agent_skills.news_dedup import ClusteredArticle

    article = NewsArticle(
        title="Gold rises",
        summary="",
        url="https://reuters.com/gold",
        canonical_url="https://reuters.com/gold",
        published_at_utc=datetime(2026, 5, 5, 12, 0, tzinfo=timezone.utc),
        source="finnhub",
        publisher="Reuters",
        symbols=(),
        keywords=(),
        impact="low",
    )
    cluster = ClusteredArticle(primary=article)
    item = NewsItem.from_cluster(cluster)
    assert item.sentiment_score is None
