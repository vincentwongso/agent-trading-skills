"""Tests for ``cfd_skills.news_dedup``."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cfd_skills.news_dedup import (
    NewsArticle,
    canonicalise_url,
    classify_impact,
    dedupe_articles,
    levenshtein,
    levenshtein_ratio,
)


def _article(
    *,
    title: str,
    url: str = "",
    publisher: str = "Reuters",
    source: str = "finnhub",
    summary: str = "",
    published: datetime | None = None,
    symbols: tuple[str, ...] = (),
) -> NewsArticle:
    return NewsArticle(
        title=title,
        summary=summary,
        url=url,
        canonical_url=canonicalise_url(url),
        published_at_utc=published or datetime(2026, 4, 29, 12, 0, tzinfo=timezone.utc),
        source=source,
        publisher=publisher,
        symbols=symbols,
        keywords=(),
        impact="low",
    )


# ---------- canonicalise_url -----------------------------------------------


def test_url_strips_utm_params() -> None:
    raw = "https://www.reuters.com/markets/?utm_source=twitter&utm_medium=social"
    assert canonicalise_url(raw) == "https://reuters.com/markets"


def test_url_strips_fbclid_and_fragment() -> None:
    raw = "https://example.com/x?fbclid=abc&q=1#section"
    assert canonicalise_url(raw) == "https://example.com/x?q=1"


def test_url_lowercases_host() -> None:
    raw = "HTTPS://Example.COM/News/"
    assert canonicalise_url(raw) == "https://example.com/News"


def test_url_keeps_meaningful_query() -> None:
    raw = "https://example.com/x?id=5&utm_source=t"
    assert canonicalise_url(raw) == "https://example.com/x?id=5"


def test_empty_url_returns_empty() -> None:
    assert canonicalise_url("") == ""


# ---------- levenshtein ----------------------------------------------------


def test_levenshtein_identical_zero() -> None:
    assert levenshtein("hello", "hello") == 0


def test_levenshtein_simple_edits() -> None:
    assert levenshtein("kitten", "sitting") == 3


def test_levenshtein_empty_string() -> None:
    assert levenshtein("", "abc") == 3
    assert levenshtein("abc", "") == 3


def test_levenshtein_ratio_normalises_case_and_punct() -> None:
    a = "Fed Holds Rates, Powell Signals Patience"
    b = "fed holds rates  powell signals patience"
    assert levenshtein_ratio(a, b) == 1.0


def test_levenshtein_ratio_below_threshold_for_different_topics() -> None:
    a = "FOMC holds rates"
    b = "ECB cuts rates by 25bps"
    assert levenshtein_ratio(a, b) < 0.85


# ---------- dedupe_articles ------------------------------------------------


def test_identical_canonical_urls_collapse() -> None:
    a = _article(title="Fed holds",
                  url="https://reuters.com/x?utm_source=a",
                  publisher="Reuters", source="finnhub")
    b = _article(title="Fed holds",
                  url="https://www.reuters.com/x?utm_source=b",
                  publisher="Reuters", source="marketaux")
    clusters = dedupe_articles([a, b])
    assert len(clusters) == 1
    assert len(clusters[0].duplicates) == 1


def test_near_duplicate_headlines_collapse() -> None:
    a = _article(
        title="Fed holds rates, signals patience",
        url="https://reuters.com/a", source="finnhub",
    )
    b = _article(
        title="Fed Holds Rates -- Signals Patience",
        url="https://yahoo.com/b", source="marketaux",
    )
    clusters = dedupe_articles([a, b])
    assert len(clusters) == 1
    assert len(clusters[0].duplicates) == 1


def test_different_topics_stay_separate() -> None:
    a = _article(title="Fed holds rates", url="https://reuters.com/a")
    b = _article(title="OPEC+ extends production cuts", url="https://reuters.com/b")
    clusters = dedupe_articles([a, b])
    assert len(clusters) == 2


def test_primary_picks_earliest_published() -> None:
    early = datetime(2026, 4, 29, 10, 0, tzinfo=timezone.utc)
    later = datetime(2026, 4, 29, 11, 0, tzinfo=timezone.utc)
    # Same canonical URL → cluster via URL bucket regardless of headline diff.
    a = _article(title="Fed holds rates", published=later, url="https://r.com/x")
    b = _article(title="Fed holds rates (UPDATE)", published=early, url="https://r.com/x")
    clusters = dedupe_articles([a, b])
    assert len(clusters) == 1
    assert clusters[0].primary.published_at_utc == early


def test_three_sources_collapse_into_one_with_three_publishers() -> None:
    base = datetime(2026, 4, 29, 10, 0, tzinfo=timezone.utc)
    # Headlines are near-identical after normalisation (case + punctuation only).
    a = _article(title="ECB cuts rates by 25 basis points",
                  url="https://reuters.com/x", source="finnhub", publisher="Reuters",
                  published=base)
    b = _article(title="ECB Cuts Rates by 25 Basis Points",
                  url="https://yahoo.com/x", source="marketaux", publisher="Yahoo",
                  published=base + timedelta(minutes=5))
    c = _article(title="ECB cuts rates by 25 basis points!",
                  url="https://bloomberg.com/x", source="forexnews", publisher="Bloomberg",
                  published=base + timedelta(minutes=10))
    clusters = dedupe_articles([a, b, c])
    assert len(clusters) == 1
    sources = clusters[0].all_sources
    assert len(sources) == 3


def test_empty_input_returns_empty() -> None:
    assert dedupe_articles([]) == []


def test_dedupe_returns_clustered_article_objects_preserving_articles() -> None:
    a = _article(title="X", url="https://r.com/x")
    b = _article(title="X update", url="https://r.com/x")  # same URL → cluster
    [c] = dedupe_articles([a, b])
    assert c.primary in c.all_articles
    assert all(art in c.all_articles for art in (a, b))


# ---------- classify_impact ------------------------------------------------


def test_classify_high_for_fomc() -> None:
    assert classify_impact("FOMC holds rates, signals patience") == "high"


def test_classify_high_for_cpi_in_summary() -> None:
    assert classify_impact("Markets brace", "US CPI release at 8:30 ET") == "high"


def test_classify_high_for_geopolitics() -> None:
    assert classify_impact("Strait of Hormuz tensions escalate") == "high"


def test_classify_medium_for_earnings() -> None:
    assert classify_impact("Apple beats Q2 earnings estimates") == "medium"


def test_classify_medium_for_pmi() -> None:
    assert classify_impact("US Manufacturing PMI rises to 51.2") == "medium"


def test_classify_low_for_opinion() -> None:
    assert classify_impact("Why I'm bullish on copper this quarter") == "low"


def test_classify_case_insensitive() -> None:
    assert classify_impact("fed cuts rates") == "high"
    assert classify_impact("FED CUTS RATES") == "high"
