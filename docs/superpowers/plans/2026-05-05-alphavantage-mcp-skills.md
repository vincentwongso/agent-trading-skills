# AlphaVantage MCP Skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate AlphaVantage MCP as a data source — macro context + sentiment enrich the existing news brief; equity fundamentals, insider/institutional, and options data ship as standalone SKILL.md-only skills.

**Architecture:** Hybrid approach — integration skills (deliverables 1-2) get Python modules with Decimal typing, tests, and CLI wiring following the existing JSON-stdin/pure-function/JSON-stdout pattern. Standalone skills (deliverables 3-5) are SKILL.md-only recipes where the agent calls AlphaVantage MCP directly. See `docs/superpowers/specs/2026-05-05-alphavantage-mcp-skills-design.md` for the full spec.

**Tech Stack:** Python 3.14, Decimal-typed pure functions, pytest, dataclasses, existing `news_dedup`/`news_brief` modules.

---

## File Structure

### New files

| File | Responsibility |
|---|---|
| `src/trading_agent_skills/macro_context.py` | `MacroReading` + `MacroContext` dataclasses, `build_macro_context()` pure function |
| `src/trading_agent_skills/av_sentiment.py` | `enrich_articles_with_sentiment()` — match AV sentiment to existing articles, add unmatched as new |
| `tests/test_macro_context.py` | Macro context direction, staleness, Decimal coercion, edge cases |
| `tests/test_av_sentiment.py` | Article matching (URL, title fuzzy, no match), sentiment field population, AV→NewsArticle conversion |
| `tests/test_news_brief_av_integration.py` | `build()` includes macro_context/top_movers when present, omits when absent |
| `.claude/skills/equity-fundamentals/SKILL.md` | Standalone skill for equity fundamental analysis |
| `.claude/skills/insider-institutional/SKILL.md` | Standalone skill for insider transactions + institutional holdings |
| `.claude/skills/options-data/SKILL.md` | Standalone skill for options chain data |

### Modified files

| File | Change |
|---|---|
| `src/trading_agent_skills/news_dedup.py` | Add 3 optional sentiment fields to `NewsArticle` |
| `src/trading_agent_skills/news_brief.py` | Add `macro_context`/`top_movers` to `NewsBriefInput` + `NewsBriefResult`, sentiment to `NewsItem` |
| `src/trading_agent_skills/cli/news.py` | Parse `macro_indicators`/`av_sentiment`/`top_movers` from bundle, wire through |
| `.claude/skills/session-news-brief/SKILL.md` | Add AV MCP fan-out steps to workflow |

---

### Task 1: Add sentiment fields to NewsArticle

**Files:**
- Modify: `src/trading_agent_skills/news_dedup.py:30-41`

- [ ] **Step 1: Add optional sentiment fields to `NewsArticle`**

In `src/trading_agent_skills/news_dedup.py`, add three optional fields at the end of the `NewsArticle` dataclass:

```python
@dataclass(frozen=True)
class NewsArticle:
    title: str
    summary: str
    url: str
    canonical_url: str
    published_at_utc: datetime
    source: str          # "finnhub" / "marketaux" / "forexnews"
    publisher: str       # "Reuters", "Bloomberg", etc.
    symbols: tuple[str, ...]   # broker-format if mappable, else raw tickers
    keywords: tuple[str, ...]
    impact: str          # "high" / "medium" / "low"
    sentiment_score: float | None = None    # AV ticker_sentiment_score (-1 to +1)
    sentiment_label: str | None = None      # "Bullish" / "Bearish" / "Neutral"
    relevance_score: float | None = None    # AV relevance_score (0 to 1)
```

- [ ] **Step 2: Verify all existing tests pass**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: All 443 tests pass. The new fields have defaults so no existing constructor calls break.

- [ ] **Step 3: Commit**

```bash
git add src/trading_agent_skills/news_dedup.py
git commit -m "feat(news): add optional sentiment fields to NewsArticle"
```

---

### Task 2: Create `macro_context.py` — dataclasses + builder

**Files:**
- Create: `src/trading_agent_skills/macro_context.py`
- Create: `tests/test_macro_context.py`

- [ ] **Step 1: Write failing tests for macro context**

Create `tests/test_macro_context.py`:

```python
"""Tests for ``trading_agent_skills.macro_context``."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from trading_agent_skills.macro_context import MacroContext, MacroReading, build_macro_context


# ---------- Fixtures --------------------------------------------------------


def _indicator(
    name: str,
    values: list[tuple[str, str]],
) -> tuple[str, list[dict[str, str]]]:
    """Return (name, data_points) pair for build_macro_context input.

    ``values`` is [(date, value), ...] in most-recent-first order
    (matching AlphaVantage's response convention).
    """
    return name, [{"date": d, "value": v} for d, v in values]


# ---------- Direction -------------------------------------------------------


def test_rising_direction() -> None:
    name, data = _indicator("CPI", [("2026-04-01", "315.0"), ("2026-03-01", "312.5")])
    ctx = build_macro_context({name: data}, reference_date=date(2026, 5, 1))
    assert len(ctx.readings) == 1
    assert ctx.readings[0].direction == "rising"
    assert ctx.readings[0].latest_value == Decimal("315.0")
    assert ctx.readings[0].previous_value == Decimal("312.5")


def test_falling_direction() -> None:
    name, data = _indicator("UNEMPLOYMENT", [("2026-04-01", "3.8"), ("2026-03-01", "4.0")])
    ctx = build_macro_context({name: data}, reference_date=date(2026, 5, 1))
    assert ctx.readings[0].direction == "falling"


def test_flat_direction() -> None:
    name, data = _indicator("FEDERAL_FUNDS_RATE", [("2026-05-01", "5.25"), ("2026-04-30", "5.25")])
    ctx = build_macro_context({name: data}, reference_date=date(2026, 5, 1))
    assert ctx.readings[0].direction == "flat"


# ---------- Multiple indicators ---------------------------------------------


def test_multiple_indicators() -> None:
    indicators = dict([
        _indicator("CPI", [("2026-04-01", "315.0"), ("2026-03-01", "312.5")]),
        _indicator("UNEMPLOYMENT", [("2026-04-01", "3.8"), ("2026-03-01", "4.0")]),
        _indicator("TREASURY_YIELD", [("2026-05-01", "4.35"), ("2026-04-30", "4.30")]),
    ])
    ctx = build_macro_context(indicators, reference_date=date(2026, 5, 1))
    assert len(ctx.readings) == 3
    names = {r.name for r in ctx.readings}
    assert names == {"CPI", "UNEMPLOYMENT", "TREASURY_YIELD"}


# ---------- Staleness -------------------------------------------------------


def test_stale_gdp_flagged() -> None:
    """GDP older than 120 days → staleness flag."""
    name, data = _indicator("REAL_GDP", [("2025-12-01", "22000"), ("2025-09-01", "21800")])
    ctx = build_macro_context({name: data}, reference_date=date(2026, 5, 1))
    assert "REAL_GDP" in ctx.staleness_flags
    assert len(ctx.readings) == 1  # reading still present, just flagged stale


def test_fresh_cpi_not_flagged() -> None:
    name, data = _indicator("CPI", [("2026-04-15", "315.0"), ("2026-03-15", "312.5")])
    ctx = build_macro_context({name: data}, reference_date=date(2026, 5, 1))
    assert "CPI" not in ctx.staleness_flags


def test_stale_daily_indicator() -> None:
    """Treasury yield older than 3 days → stale."""
    name, data = _indicator("TREASURY_YIELD", [("2026-04-25", "4.35"), ("2026-04-24", "4.30")])
    ctx = build_macro_context({name: data}, reference_date=date(2026, 5, 1))
    assert "TREASURY_YIELD" in ctx.staleness_flags


# ---------- Edge cases ------------------------------------------------------


def test_missing_value_dot_skipped() -> None:
    """AlphaVantage uses '.' for missing values — skip those data points."""
    data = [
        {"date": "2026-04-01", "value": "."},
        {"date": "2026-03-01", "value": "312.5"},
        {"date": "2026-02-01", "value": "310.0"},
    ]
    ctx = build_macro_context({"CPI": data}, reference_date=date(2026, 5, 1))
    assert len(ctx.readings) == 1
    assert ctx.readings[0].latest_value == Decimal("312.5")
    assert ctx.readings[0].previous_value == Decimal("310.0")


def test_insufficient_data_produces_staleness_flag() -> None:
    """Fewer than 2 valid data points → no reading, staleness flag."""
    data = [{"date": "2026-04-01", "value": "315.0"}]
    ctx = build_macro_context({"CPI": data}, reference_date=date(2026, 5, 1))
    assert len(ctx.readings) == 0
    assert "CPI" in ctx.staleness_flags


def test_empty_indicators_dict() -> None:
    ctx = build_macro_context({}, reference_date=date(2026, 5, 1))
    assert len(ctx.readings) == 0
    assert len(ctx.staleness_flags) == 0


def test_decimal_coercion_from_string() -> None:
    """Values arrive as strings from AV — must coerce to Decimal, not float."""
    name, data = _indicator("CPI", [("2026-04-01", "315.123"), ("2026-03-01", "312.456")])
    ctx = build_macro_context({name: data}, reference_date=date(2026, 5, 1))
    reading = ctx.readings[0]
    assert isinstance(reading.latest_value, Decimal)
    assert isinstance(reading.previous_value, Decimal)
    assert reading.latest_value == Decimal("315.123")


def test_unknown_indicator_no_staleness_check() -> None:
    """Indicators not in the cadence table still produce readings, no staleness."""
    name, data = _indicator("CUSTOM_THING", [("2026-04-01", "100"), ("2026-03-01", "99")])
    ctx = build_macro_context({name: data}, reference_date=date(2026, 5, 1))
    assert len(ctx.readings) == 1
    assert "CUSTOM_THING" not in ctx.staleness_flags
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_macro_context.py -v`
Expected: ImportError — `macro_context` module doesn't exist yet.

- [ ] **Step 3: Implement `macro_context.py`**

Create `src/trading_agent_skills/macro_context.py`:

```python
"""Macro economic context from AlphaVantage economic indicator APIs.

Extracts latest + previous readings for each indicator, computes directional
change, and detects staleness based on expected update cadence. All values
are Decimal-typed via ``D()`` — no floats cross this boundary.

This module is pure — no I/O. The agent fetches AV MCP tool outputs and
passes the raw response data in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from trading_agent_skills.decimal_io import D


# Expected update cadence per indicator (used for staleness detection).
# If the latest reading is older than this relative to the reference date,
# the indicator is flagged stale.
_EXPECTED_CADENCE: dict[str, timedelta] = {
    "TREASURY_YIELD": timedelta(days=3),
    "FEDERAL_FUNDS_RATE": timedelta(days=3),
    "CPI": timedelta(days=45),
    "INFLATION": timedelta(days=400),
    "UNEMPLOYMENT": timedelta(days=45),
    "NONFARM_PAYROLL": timedelta(days=45),
    "REAL_GDP": timedelta(days=120),
    "RETAIL_SALES": timedelta(days=45),
    "DURABLES": timedelta(days=45),
}


@dataclass(frozen=True)
class MacroReading:
    name: str
    latest_value: Decimal
    latest_date: str
    previous_value: Decimal
    previous_date: str
    direction: str  # "rising" / "falling" / "flat"


@dataclass(frozen=True)
class MacroContext:
    readings: tuple[MacroReading, ...]
    staleness_flags: tuple[str, ...]


def _parse_reading(
    name: str,
    data_points: list[dict[str, str]],
) -> MacroReading | None:
    """Extract latest + previous from AV's data point list.

    Each item is ``{"date": "YYYY-MM-DD", "value": "123.456"}``.
    AV uses ``"."`` for missing values — those are skipped.
    Returns ``None`` when fewer than 2 valid points exist.
    """
    valid = [dp for dp in data_points if dp.get("value") and dp["value"] != "."]
    if len(valid) < 2:
        return None
    latest = valid[0]
    previous = valid[1]
    latest_val = D(latest["value"])
    prev_val = D(previous["value"])
    if latest_val > prev_val:
        direction = "rising"
    elif latest_val < prev_val:
        direction = "falling"
    else:
        direction = "flat"
    return MacroReading(
        name=name,
        latest_value=latest_val,
        latest_date=latest["date"],
        previous_value=prev_val,
        previous_date=previous["date"],
        direction=direction,
    )


def build_macro_context(
    indicators: dict[str, list[dict[str, str]]],
    *,
    reference_date: date | None = None,
) -> MacroContext:
    """Build macro context from raw AV economic indicator responses.

    Args:
        indicators: indicator name → list of data points (most-recent first).
        reference_date: date to check staleness against (defaults to today).
    """
    ref = reference_date or date.today()
    readings: list[MacroReading] = []
    stale: list[str] = []

    for name, data_points in indicators.items():
        reading = _parse_reading(name, data_points)
        if reading is None:
            stale.append(name)
            continue
        readings.append(reading)
        cadence = _EXPECTED_CADENCE.get(name)
        if cadence and ref - date.fromisoformat(reading.latest_date) > cadence:
            stale.append(name)

    return MacroContext(
        readings=tuple(readings),
        staleness_flags=tuple(stale),
    )


__all__ = ["MacroReading", "MacroContext", "build_macro_context"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_macro_context.py -v`
Expected: All 11 tests pass.

- [ ] **Step 5: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: All tests pass (443 + 11 = 454).

- [ ] **Step 6: Commit**

```bash
git add src/trading_agent_skills/macro_context.py tests/test_macro_context.py
git commit -m "feat(macro): add macro_context module with direction + staleness detection"
```

---

### Task 3: Create `av_sentiment.py` — sentiment enrichment

**Files:**
- Create: `src/trading_agent_skills/av_sentiment.py`
- Create: `tests/test_av_sentiment.py`

- [ ] **Step 1: Write failing tests for sentiment enrichment**

Create `tests/test_av_sentiment.py`:

```python
"""Tests for ``trading_agent_skills.av_sentiment``."""

from __future__ import annotations

from datetime import datetime, timezone

from trading_agent_skills.av_sentiment import (
    enrich_articles_with_sentiment,
)
from trading_agent_skills.news_dedup import NewsArticle, canonicalise_url


# ---------- Fixtures --------------------------------------------------------


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


# ---------- URL matching ----------------------------------------------------


def test_match_by_url() -> None:
    """AV entry with same URL as existing article → enrich, don't duplicate."""
    articles = [_article(title="Gold rises", url="https://reuters.com/gold")]
    av = [_av_entry(url="https://reuters.com/gold", overall_sentiment_score=0.5)]
    result = enrich_articles_with_sentiment(articles, av)
    assert len(result) == 1
    assert result[0].sentiment_score == 0.5
    assert result[0].title == "Gold rises"  # original title preserved


def test_match_by_url_canonical() -> None:
    """URL matching uses canonical form — tracking params stripped."""
    articles = [_article(title="Gold rises", url="https://reuters.com/gold")]
    av = [_av_entry(url="https://reuters.com/gold?utm_source=twitter")]
    result = enrich_articles_with_sentiment(articles, av)
    assert len(result) == 1
    assert result[0].sentiment_score is not None


# ---------- Title matching --------------------------------------------------


def test_match_by_title_similarity() -> None:
    """Different URLs but near-identical titles → match."""
    articles = [_article(title="Gold rises on safe-haven demand", url="https://reuters.com/a")]
    av = [_av_entry(title="Gold Rises On Safe-Haven Demand", url="https://cnbc.com/b")]
    result = enrich_articles_with_sentiment(articles, av)
    assert len(result) == 1
    assert result[0].sentiment_score is not None


def test_no_match_different_title() -> None:
    """Very different title and URL → AV entry added as new article."""
    articles = [_article(title="Oil falls on weak demand")]
    av = [_av_entry(title="Fed holds rates steady", url="https://cnbc.com/fed")]
    result = enrich_articles_with_sentiment(articles, av)
    assert len(result) == 2
    new = result[1]
    assert new.source == "alphavantage"
    assert new.title == "Fed holds rates steady"


# ---------- Ticker sentiment ------------------------------------------------


def test_ticker_sentiment_used_when_available() -> None:
    """Per-ticker sentiment preferred over overall when ticker matches."""
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


# ---------- Unmatched AV entries → new articles -----------------------------


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
    """AV time format YYYYMMDDTHHMMSS is correctly parsed."""
    av = [_av_entry(time_published="20260101T093015", url="https://x.com/1")]
    result = enrich_articles_with_sentiment([], av)
    assert result[0].published_at_utc == datetime(2026, 1, 1, 9, 30, 15, tzinfo=timezone.utc)


# ---------- Does not mutate input -------------------------------------------


def test_original_list_not_mutated() -> None:
    articles = [_article(title="Gold rises", url="https://reuters.com/gold")]
    av = [_av_entry(url="https://reuters.com/gold")]
    original_len = len(articles)
    result = enrich_articles_with_sentiment(articles, av)
    assert len(articles) == original_len
    assert articles[0].sentiment_score is None  # original unchanged


# ---------- Empty inputs ----------------------------------------------------


def test_empty_articles_empty_av() -> None:
    assert enrich_articles_with_sentiment([], []) == []


def test_empty_av_returns_articles_unchanged() -> None:
    articles = [_article(title="Gold rises")]
    result = enrich_articles_with_sentiment(articles, [])
    assert len(result) == 1
    assert result[0].sentiment_score is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_av_sentiment.py -v`
Expected: ImportError — `av_sentiment` module doesn't exist yet.

- [ ] **Step 3: Implement `av_sentiment.py`**

Create `src/trading_agent_skills/av_sentiment.py`:

```python
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
    """Find the best matching article index for an AV sentiment entry.

    Stage 1: canonical URL match (exact).
    Stage 2: normalised headline similarity (>= 0.85 threshold).
    Returns ``None`` when no match is found.
    """
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
    """Extract (sentiment_score, sentiment_label, relevance_score).

    Prefers per-ticker sentiment from ``ticker_sentiment`` array (first entry).
    Falls back to ``overall_sentiment_score`` / ``overall_sentiment_label``.
    """
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
    """Convert an unmatched AV sentiment entry to a ``NewsArticle``."""
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
    """Match AV sentiment entries to existing articles, enrich or add new.

    - Matched: ``sentiment_score``, ``sentiment_label``, ``relevance_score``
      are set on a copy of the original article.
    - Unmatched: converted to a new ``NewsArticle`` with source ``"alphavantage"``.

    Returns a new list; never mutates the input.
    """
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_av_sentiment.py -v`
Expected: All 10 tests pass.

- [ ] **Step 5: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: All tests pass (454 + 10 = 464).

- [ ] **Step 6: Commit**

```bash
git add src/trading_agent_skills/av_sentiment.py tests/test_av_sentiment.py
git commit -m "feat(sentiment): add av_sentiment module for AV NEWS_SENTIMENT enrichment"
```

---

### Task 4: Integrate macro context + top movers + sentiment into `news_brief.py`

**Files:**
- Modify: `src/trading_agent_skills/news_brief.py:62-76` (NewsBriefInput)
- Modify: `src/trading_agent_skills/news_brief.py:128-141` (NewsBriefResult)
- Modify: `src/trading_agent_skills/news_brief.py:91-111` (NewsItem)
- Modify: `src/trading_agent_skills/news_brief.py:440-517` (build)
- Create: `tests/test_news_brief_av_integration.py`

- [ ] **Step 1: Write failing integration tests**

Create `tests/test_news_brief_av_integration.py`:

```python
"""Tests for AlphaVantage integration in ``trading_agent_skills.news_brief``."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from trading_agent_skills.macro_context import MacroContext, MacroReading
from trading_agent_skills.news_brief import NewsBriefInput, build
from trading_agent_skills.news_dedup import NewsArticle, canonicalise_url
from trading_agent_skills.watchlist import resolve_watchlist


# ---------- Fixtures --------------------------------------------------------


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


# ---------- Macro context ---------------------------------------------------


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


# ---------- Top movers ------------------------------------------------------


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


# ---------- Sentiment on NewsItem -------------------------------------------


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_news_brief_av_integration.py -v`
Expected: TypeError — `NewsBriefInput` doesn't accept `macro_context`/`top_movers` yet.

- [ ] **Step 3: Add `macro_context` and `top_movers` to `NewsBriefInput`**

In `src/trading_agent_skills/news_brief.py`, add the import at the top (after the existing imports):

```python
from trading_agent_skills.macro_context import MacroContext
```

Then add two optional fields at the end of `NewsBriefInput`:

```python
@dataclass(frozen=True)
class NewsBriefInput:
    now_utc: datetime
    lookahead_hours: int
    lookback_hours: int
    watchlist: WatchlistResolution
    bars_by_symbol: Mapping[str, list[Bar]]
    symbol_meta: Mapping[str, SymbolMeta]
    economic_events: list[CalixEconomicEvent]
    earnings_entries: list[CalixEarningsEntry]
    economic_stale: bool
    earnings_stale: bool
    articles_by_provider: Mapping[str, list[NewsArticle]]
    provider_status: Mapping[str, str]
    macro_context: MacroContext | None = None
    top_movers: dict | None = None
```

- [ ] **Step 4: Add sentiment fields to `NewsItem` and update `from_cluster`**

In `src/trading_agent_skills/news_brief.py`, add three optional fields to `NewsItem` and update `from_cluster`:

```python
@dataclass(frozen=True)
class NewsItem:
    title: str
    publisher: str
    sources: tuple[str, ...]
    url: str
    published_at_utc: str
    impact: str
    summary: str
    sentiment_score: float | None = None
    sentiment_label: str | None = None
    relevance_score: float | None = None

    @classmethod
    def from_cluster(cls, c: ClusteredArticle) -> "NewsItem":
        primary = c.primary
        return cls(
            title=primary.title,
            publisher=primary.publisher,
            sources=c.all_sources,
            url=primary.url,
            published_at_utc=primary.published_at_utc.isoformat(),
            impact=primary.impact,
            summary=(primary.summary or "")[:300],
            sentiment_score=primary.sentiment_score,
            sentiment_label=primary.sentiment_label,
            relevance_score=primary.relevance_score,
        )
```

- [ ] **Step 5: Add `macro_context` and `top_movers` to `NewsBriefResult`**

Add two optional fields at the end of `NewsBriefResult`:

```python
@dataclass
class NewsBriefResult:
    generated_at_utc: str
    lookahead_hours: int
    lookback_hours: int
    watchlist: list[str]
    watchlist_description: str
    watchlist_by_tier: dict[str, list[str]]
    calendar_by_symbol: dict[str, list[CalendarItem]]
    news_by_symbol: dict[str, list[NewsItem]]
    swing_candidates: list[SwingCandidate]
    health: dict[str, str]
    flags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    macro_context: MacroContext | None = None
    top_movers: dict | None = None
```

- [ ] **Step 6: Wire macro context + top movers through `build()`**

In the `build()` function, add macro staleness flag detection before the return statement, and include the new fields in the returned `NewsBriefResult`:

After the existing `CALIX_DEGRADED` block (around line 500) and before the `return` statement, add:

```python
    if inp.macro_context is not None and inp.macro_context.staleness_flags:
        flags.append("AV_MACRO_STALE")
        notes.append(
            "Stale macro indicators: "
            + ", ".join(inp.macro_context.staleness_flags)
            + " — latest readings may be outdated."
        )
```

Then update the return statement to include the new fields:

```python
    return NewsBriefResult(
        generated_at_utc=now_utc.isoformat(),
        lookahead_hours=inp.lookahead_hours,
        lookback_hours=inp.lookback_hours,
        watchlist=list(inp.watchlist.symbols),
        watchlist_description=inp.watchlist.description,
        watchlist_by_tier={
            k: list(v) for k, v in inp.watchlist.by_tier.items()
        },
        calendar_by_symbol=calendar_by_symbol,
        news_by_symbol=news_by_symbol,
        swing_candidates=swing_candidates,
        health=health,
        flags=flags,
        notes=notes,
        macro_context=inp.macro_context,
        top_movers=inp.top_movers,
    )
```

- [ ] **Step 7: Update `__all__` in `news_brief.py`**

Add `"MacroContext"` re-export is not needed since it's imported from `macro_context.py`. No `__all__` change required — the import is internal.

- [ ] **Step 8: Run integration tests**

Run: `.venv/bin/python -m pytest tests/test_news_brief_av_integration.py -v`
Expected: All 8 tests pass.

- [ ] **Step 9: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: All tests pass (464 + 8 = 472).

- [ ] **Step 10: Commit**

```bash
git add src/trading_agent_skills/news_brief.py tests/test_news_brief_av_integration.py
git commit -m "feat(brief): integrate macro context, top movers, and sentiment into news brief"
```

---

### Task 5: Wire AlphaVantage data through `cli/news.py`

**Files:**
- Modify: `src/trading_agent_skills/cli/news.py:280-377`

- [ ] **Step 1: Add imports at the top of `cli/news.py`**

After the existing imports, add:

```python
from trading_agent_skills.av_sentiment import enrich_articles_with_sentiment
from trading_agent_skills.macro_context import build_macro_context
```

- [ ] **Step 2: Parse new bundle keys and wire through in `main()`**

In the `main()` function, after the news parsing block (after line 355 where `articles_by_provider` and `provider_status` are set) and before the `except` clause, add:

```python
        # AlphaVantage sentiment enrichment
        av_sentiment_raw = bundle.get("av_sentiment")
        if av_sentiment_raw:
            flat = [a for arts in articles_by_provider.values() for a in arts]
            enriched = enrich_articles_with_sentiment(flat, av_sentiment_raw)
            articles_by_provider = {"all": enriched}
            provider_status["alphavantage"] = "ok"

        # AlphaVantage macro indicators
        macro_raw = bundle.get("macro_indicators")
        macro_ctx = None
        if macro_raw:
            macro_ctx = build_macro_context(macro_raw)

        # AlphaVantage top movers
        top_movers = bundle.get("top_movers")
```

Then update the `NewsBriefInput` construction (around line 360) to include the new fields:

```python
    inp = NewsBriefInput(
        now_utc=now_utc,
        lookahead_hours=lookahead,
        lookback_hours=lookback,
        watchlist=watchlist_res,
        bars_by_symbol=bars_by_symbol,
        symbol_meta=symbol_meta,
        economic_events=economic_events,
        earnings_entries=earnings_entries,
        economic_stale=economic_stale,
        earnings_stale=earnings_stale,
        articles_by_provider=articles_by_provider,
        provider_status=provider_status,
        macro_context=macro_ctx,
        top_movers=top_movers,
    )
```

- [ ] **Step 3: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: All 472 tests pass.

- [ ] **Step 4: Commit**

```bash
git add src/trading_agent_skills/cli/news.py
git commit -m "feat(cli): wire AV macro, sentiment, and top movers through news CLI"
```

---

### Task 6: Update `session-news-brief/SKILL.md`

**Files:**
- Modify: `.claude/skills/session-news-brief/SKILL.md`

- [ ] **Step 1: Update the skill description in frontmatter**

Update the `description` field to mention AlphaVantage:

```yaml
---
name: session-news-brief
description: Use when the user wants a session-start brief, asks what's happening on a specific symbol, requests overnight news that moved markets, or asks for swing-trade candidates. Triggers on phrases like "morning brief", "news brief for the session", "what's happening on EURUSD", "any news on [symbol] in the last [N] hours", "any swing setups today", "what's moving the metals overnight". Composes Calix calendar + 3-API news fan-out (Finnhub / Marketaux / ForexNews) + AlphaVantage macro context & sentiment + ATR/RSI swing-candidates section. Read-only / advisory — never executes.
---
```

- [ ] **Step 2: Update the intro paragraph**

Replace the first paragraph with:

```markdown
Combines economic + earnings calendar (Calix), a fan-out across three news APIs with cross-publisher dedup, AlphaVantage macro economic context + NLP sentiment enrichment + top movers, and a swing-candidates section that surfaces positive-carry setups at technical extremes.
```

- [ ] **Step 3: Add AlphaVantage MCP prerequisite**

After prerequisite 5 (config.toml), add:

```markdown
6. **AlphaVantage MCP server (optional).** If the `alphavantage` MCP server is configured, the brief gains three sections: macro economic context (GDP, CPI, yields, unemployment, etc.), NLP sentiment scores on news articles, and top equity gainers/losers/most-active. If the server isn't configured, these sections are silently omitted — the brief runs unchanged.
```

- [ ] **Step 4: Add AV fan-out step between steps 2 and 3 in the workflow**

After "### 2. Gather Calix calendar" and before "### 3. Fan out to news providers", insert:

```markdown
### 2b. Fan out AlphaVantage MCP tools (optional, parallel)

If the AlphaVantage MCP server is configured, call these tools in parallel:

**Macro indicators** (one call each):
- `mcp__alphavantage__TREASURY_YIELD(interval="daily", maturity="10year")`
- `mcp__alphavantage__FEDERAL_FUNDS_RATE(interval="daily")`
- `mcp__alphavantage__CPI(interval="monthly")`
- `mcp__alphavantage__INFLATION(interval="annual")`
- `mcp__alphavantage__UNEMPLOYMENT(interval="monthly")`
- `mcp__alphavantage__NONFARM_PAYROLL(interval="monthly")`
- `mcp__alphavantage__REAL_GDP(interval="quarterly")`
- `mcp__alphavantage__RETAIL_SALES(interval="monthly")`
- `mcp__alphavantage__DURABLES(interval="monthly")`

**Sentiment** (one call per watchlist symbol):
- `mcp__alphavantage__NEWS_SENTIMENT(tickers=<symbol>, time_from=<lookback_iso>)`

**Top movers** (single call):
- `mcp__alphavantage__TOP_GAINERS_LOSERS()`

If any AV call fails or the MCP server is not configured, skip silently — existing pipeline runs unchanged.
```

- [ ] **Step 5: Update the bundle JSON example**

In "### 4. Build the bundle", add the new keys to the example JSON, after the `"calix"` block:

```json
  "macro_indicators": {
    "TREASURY_YIELD": [{"date": "2026-05-05", "value": "4.35"}, {"date": "2026-05-02", "value": "4.30"}],
    "CPI": [{"date": "2026-04-01", "value": "315.0"}, {"date": "2026-03-01", "value": "312.5"}],
    "FEDERAL_FUNDS_RATE": [{"date": "2026-05-05", "value": "5.25"}, {"date": "2026-05-02", "value": "5.25"}]
  },

  "av_sentiment": [
    {
      "title": "Gold rises on safe-haven demand",
      "url": "https://reuters.com/gold",
      "time_published": "20260505T120000",
      "source": "Reuters",
      "overall_sentiment_score": 0.35,
      "overall_sentiment_label": "Somewhat-Bullish",
      "ticker_sentiment": [
        {"ticker": "XAUUSD", "relevance_score": "0.9",
         "ticker_sentiment_score": "0.5", "ticker_sentiment_label": "Bullish"}
      ]
    }
  ],

  "top_movers": {
    "top_gainers": [{"ticker": "AAPL", "price": "195.20", "change_percentage": "5.2%"}],
    "top_losers": [{"ticker": "TSLA", "price": "172.50", "change_percentage": "-3.1%"}],
    "most_actively_traded": [{"ticker": "NVDA", "price": "890.00", "volume": 85000000}]
  }
```

- [ ] **Step 6: Update the render example**

Add macro context and top movers sections to the JSON output example:

```json
  "macro_context": {
    "readings": [
      {"name": "TREASURY_YIELD", "latest_value": "4.35", "latest_date": "2026-05-05",
       "previous_value": "4.30", "previous_date": "2026-05-02", "direction": "rising"},
      {"name": "CPI", "latest_value": "315.0", "latest_date": "2026-04-01",
       "previous_value": "312.5", "previous_date": "2026-03-01", "direction": "rising"}
    ],
    "staleness_flags": []
  },
  "top_movers": {
    "top_gainers": [...],
    "top_losers": [...],
    "most_actively_traded": [...]
  }
```

And add to the rendered text example:

```
📊 Macro Context:
  Treasury Yield (10Y): 4.35% (↑ from 4.30%)
  CPI: 315.0 (↑ from 312.5)
  Fed Funds Rate: 5.25% (→ unchanged)

📈 Top Movers:
  Gainers: AAPL +5.2%, MSFT +3.1%
  Losers: TSLA -3.1%, META -2.5%
```

- [ ] **Step 7: Update health section**

Add to the health/degraded-modes list:

```markdown
- `AV_MACRO_STALE` — one or more macro indicators are older than their expected update cadence (e.g. GDP more than 120 days old). The reading is still shown but flagged.
```

- [ ] **Step 8: Commit**

```bash
git add .claude/skills/session-news-brief/SKILL.md
git commit -m "feat(skill): add AlphaVantage macro, sentiment, top movers to news brief skill"
```

---

### Task 7: Create `equity-fundamentals` SKILL.md

**Files:**
- Create: `.claude/skills/equity-fundamentals/SKILL.md`

- [ ] **Step 1: Write the skill file**

Create `.claude/skills/equity-fundamentals/SKILL.md`:

```markdown
---
name: equity-fundamentals
description: Use when the user asks for equity fundamental analysis, company financials, P/E ratio, earnings, balance sheet, income statement, or cash flow for a stock ticker. Triggers on phrases like "fundamentals for AAPL", "show me TSLA's balance sheet", "what's NVDA's P/E ratio", "income statement for MSFT", "how's AMZN doing financially", "compare AAPL and MSFT fundamentals". Calls AlphaVantage MCP tools directly — no Python CLI layer. Read-only / advisory — never executes trades.
---

# Equity Fundamentals

Fetches and renders company fundamentals from AlphaVantage MCP: overview, income statement, balance sheet, cash flow, and earnings data. Covers US-listed equities and ETFs.

This skill never executes trades — output is informational. Use alongside [`insider-institutional`](../insider-institutional/SKILL.md) for smart-money context, or [`price-action`](../price-action/SKILL.md) for technical structure.

## Prerequisites

1. **AlphaVantage MCP server is configured.** Verify with `mcp__alphavantage__PING`. If not configured, tell the user: "The equity-fundamentals skill requires the AlphaVantage MCP server. Add it to your MCP configuration with your API key."

## When to invoke

- "fundamentals for AAPL" / "financials for TSLA"
- "show me NVDA's balance sheet"
- "what's MSFT's P/E ratio" / "earnings for AMZN"
- "income statement for GOOG"
- "how's META doing financially"
- "compare AAPL and MSFT fundamentals"

Don't invoke for: price/quote data (use `mt5-market-data`), news/sentiment (use `session-news-brief`), insider/institutional activity (use `insider-institutional`), options pricing (use `options-data`).

## Inputs

1. **Symbol(s)** — required. One or more equity tickers. If ambiguous, resolve with `mcp__alphavantage__SYMBOL_SEARCH(keywords=<query>)`.

## Workflow

### 1. Resolve symbol

If the user says a company name instead of a ticker (e.g. "Apple"), call:
- `mcp__alphavantage__SYMBOL_SEARCH(keywords="Apple")`
Pick the best match and confirm with the user if ambiguous.

### 2. Fan out MCP calls (parallel)

For each symbol, call all 5 in parallel:
- `mcp__alphavantage__COMPANY_OVERVIEW(symbol=<sym>)`
- `mcp__alphavantage__INCOME_STATEMENT(symbol=<sym>)`
- `mcp__alphavantage__BALANCE_SHEET(symbol=<sym>)`
- `mcp__alphavantage__CASH_FLOW(symbol=<sym>)`
- `mcp__alphavantage__EARNINGS(symbol=<sym>)`

### 3. Render

Structure the output as:

```
📊 AAPL — Apple Inc. (Technology / Consumer Electronics)

Overview:
  Market Cap: $3.1T | P/E: 32.5 | EPS: $6.42
  Dividend Yield: 0.52% | Beta: 1.24
  52-Week: $164.08 – $199.62

Income (Latest Quarter — Q1 2026):
  Revenue: $124.3B (+8.2% YoY) | Net Income: $36.3B
  Operating Margin: 33.2% | EBITDA: $45.1B
  Trend (last 4Q): $110.5B → $85.8B → $94.9B → $124.3B

Balance Sheet:
  Total Assets: $352.6B | Total Liabilities: $290.4B
  Debt-to-Equity: 4.67 | Current Ratio: 0.99
  Cash & Equivalents: $29.9B

Cash Flow:
  Operating CF: $39.9B | Free CF: $28.5B
  CapEx: $11.4B | FCF Margin: 22.9%

Earnings (Last 4 Quarters):
  Q1 2026: $2.10 actual vs $2.05 est (+2.4% surprise) ✅
  Q4 2025: $1.64 actual vs $1.60 est (+2.5% surprise) ✅
  Q3 2025: $1.46 actual vs $1.35 est (+8.1% surprise) ✅
  Q2 2025: $1.40 actual vs $1.34 est (+4.5% surprise) ✅

Notable: Earnings beat 4 of last 4 quarters. Debt-to-equity 4.67 is
elevated but typical for AAPL's buyback-funded capital structure.
```

### 4. Multi-symbol comparison

For "compare AAPL and MSFT", fan out all 10 calls (5 per symbol) in parallel. Render side-by-side:

```
          AAPL           MSFT
P/E       32.5           35.2
EPS       $6.42          $12.15
Div Yld   0.52%          0.73%
D/E       4.67           0.42
FCF       $28.5B         $59.5B
Beat Rate 4/4            3/4
```

### 5. Highlight notable items

Flag any of:
- Earnings beat/miss streak (3+ consecutive)
- Debt-to-equity above 2.0
- Negative free cash flow
- Revenue declining quarter-over-quarter
- P/E significantly above/below sector average (if sector data available)
```

- [ ] **Step 2: Commit**

```bash
git add .claude/skills/equity-fundamentals/SKILL.md
git commit -m "feat(skill): add equity-fundamentals standalone skill (SKILL.md-only)"
```

---

### Task 8: Create `insider-institutional` SKILL.md

**Files:**
- Create: `.claude/skills/insider-institutional/SKILL.md`

- [ ] **Step 1: Write the skill file**

Create `.claude/skills/insider-institutional/SKILL.md`:

```markdown
---
name: insider-institutional
description: Use when the user asks about insider transactions, institutional holdings, smart money flow, or who is buying/selling a stock. Triggers on phrases like "any insider activity on AAPL", "who's buying TSLA shares", "institutional holders of NVDA", "insider transactions for MSFT this quarter", "show me smart money on AMZN". Calls AlphaVantage MCP tools directly — no Python CLI layer. Read-only / advisory — never executes trades.
---

# Insider & Institutional

Fetches insider transactions and institutional holdings from AlphaVantage MCP. Shows who is buying/selling inside the company and which institutions are accumulating or reducing positions.

This skill never executes trades — output is informational. Use alongside [`equity-fundamentals`](../equity-fundamentals/SKILL.md) for financial health context.

## Prerequisites

1. **AlphaVantage MCP server is configured.** Verify with `mcp__alphavantage__PING`. If not configured, tell the user: "The insider-institutional skill requires the AlphaVantage MCP server. Add it to your MCP configuration with your API key."

## When to invoke

- "any insider activity on AAPL"
- "who's buying TSLA shares" / "insider transactions for MSFT"
- "institutional holders of NVDA" / "who holds GOOG"
- "show me smart money on AMZN"
- "compare insider activity AAPL vs MSFT"

Don't invoke for: company financials (use `equity-fundamentals`), price data (use `mt5-market-data`), options (use `options-data`).

## Inputs

1. **Symbol(s)** — required. If ambiguous, resolve with `mcp__alphavantage__SYMBOL_SEARCH(keywords=<query>)`.

## Workflow

### 1. Resolve symbol

Same as equity-fundamentals — use `SYMBOL_SEARCH` if the user gives a company name.

### 2. Fan out MCP calls (parallel)

For each symbol:
- `mcp__alphavantage__INSIDER_TRANSACTIONS(symbol=<sym>)`
- `mcp__alphavantage__INSTITUTIONAL_HOLDINGS(symbol=<sym>)`

### 3. Render

```
🏢 AAPL — Insider & Institutional Activity

Insider Transactions (Last 90 Days):
  2026-04-15  Tim Cook (CEO)         SELL   50,000 shares  $9.76M  [now holds 3.28M]
  2026-04-01  Luca Maestri (CFO)     SELL   25,000 shares  $4.87M  [now holds 1.12M]
  2026-03-20  Jeff Williams (COO)    BUY    10,000 shares  $1.93M  [now holds 489K]
  2026-03-15  Deirdre O'Brien (SVP)  EXERCISE+SELL 30,000  $5.82M

  Pattern: Mixed — CEO/CFO selling (routine 10b5-1), COO buying.

Top Institutional Holders:
  #  Institution              Shares      Value       Weight  QoQ Change
  1  Vanguard Group           1.28B      $248.6B     8.1%    +0.3%
  2  BlackRock                1.02B      $198.1B     6.5%    +0.1%
  3  Berkshire Hathaway       915M       $177.6B     5.8%    -2.1% ⬇
  4  State Street             603M       $117.0B     3.8%    +0.5%
  5  FMR LLC                  350M       $67.9B      2.2%    +1.2% ⬆

Signal: Net insider selling (mostly routine/scheduled). Institutional
accumulation by Vanguard/State Street/FMR; Berkshire trimming 2.1%.
```

### 4. Multi-symbol comparison

For "compare insider activity AAPL vs MSFT", fan out all 4 calls (2 per symbol) in parallel. Render each symbol's section, then a comparison summary:

```
Signal Comparison:
  AAPL: Net insider selling (routine), institutional accumulation
  MSFT: Net insider buying (CEO + CFO), mixed institutional flow
```

### 5. Flag notable patterns

- Cluster of insider buys (3+ insiders buying within 30 days)
- Large insider buy by CEO/CFO (> $1M)
- Insider sells at 52-week high
- New institutional position (0 → significant holding)
- Major institution exiting entirely
- Insider buy/sell ratio shifts
```

- [ ] **Step 2: Commit**

```bash
git add .claude/skills/insider-institutional/SKILL.md
git commit -m "feat(skill): add insider-institutional standalone skill (SKILL.md-only)"
```

---

### Task 9: Create `options-data` SKILL.md

**Files:**
- Create: `.claude/skills/options-data/SKILL.md`

- [ ] **Step 1: Write the skill file**

Create `.claude/skills/options-data/SKILL.md`:

```markdown
---
name: options-data
description: Use when the user asks for options chain data, options pricing, implied volatility, Greeks, fair market value, or historical options for a symbol. Triggers on phrases like "options chain for SPY", "show me AAPL options", "what's the options pricing on TSLA", "historical options for NVDA on 2026-04-30", "fair value on SPY options", "IV on AAPL calls", "raw options data for SPY". Calls AlphaVantage MCP tools directly — no Python CLI layer. Designed as a raw data access layer for future ML/quant consumption. Read-only / advisory — never executes trades.
---

# Options Data

Raw data access layer for US equity options via AlphaVantage MCP. Exposes realtime chains with Greeks, fair market value marks, and historical snapshots. Designed for inspection now and ML/quant pipeline consumption later.

This skill never executes trades — output is informational.

## Prerequisites

1. **AlphaVantage MCP server is configured.** Verify with `mcp__alphavantage__PING`. If not configured, tell the user: "The options-data skill requires the AlphaVantage MCP server. Add it to your MCP configuration with your API key."

## When to invoke

- "options chain for SPY" / "show me AAPL options"
- "what's the options pricing on TSLA"
- "historical options for NVDA on 2026-04-30"
- "fair value on SPY options"
- "IV on AAPL calls"
- "raw options data for SPY"
- "put/call ratio for QQQ"

Don't invoke for: stock price/quote (use `mt5-market-data`), company financials (use `equity-fundamentals`), insider activity (use `insider-institutional`).

## Inputs

1. **Symbol** — required.
2. **Date** — optional, for historical queries only (ISO format).
3. **Contract** — optional, for specific contract lookup.

## Workflow

### 1. Resolve symbol

Same as other skills — use `mcp__alphavantage__SYMBOL_SEARCH` if ambiguous.

### 2. Determine which tools to call

| User request | Tools |
|---|---|
| "options chain" / "options on X" | `REALTIME_OPTIONS` + `REALTIME_OPTIONS_FMV` (parallel) |
| "historical options on [date]" | `HISTORICAL_OPTIONS(symbol, date)` |
| "full options picture" | All three (parallel) |
| "raw options data" | `REALTIME_OPTIONS` (full output, minimal formatting) |
| Specific contract | `REALTIME_OPTIONS(symbol, contract=<id>)` |

### 3. Fan out MCP calls

- `mcp__alphavantage__REALTIME_OPTIONS(symbol=<sym>)` — current chain with Greeks
- `mcp__alphavantage__REALTIME_OPTIONS_FMV(symbol=<sym>)` — fair market value marks
- `mcp__alphavantage__HISTORICAL_OPTIONS(symbol=<sym>, date=<date>)` — historical snapshot

### 4. Render (standard mode)

Show calls and puts for the nearest 3 expirations:

```
📋 SPY Options Chain — as of 2026-05-05 15:45 ET

Expiry: 2026-05-09 (4 DTE)
Strike  | Type | Bid    | Ask    | IV     | Delta  | OI      | Volume
--------|------|--------|--------|--------|--------|---------|-------
520     | Call | 3.45   | 3.52   | 18.2%  | 0.62   | 45,230  | 12,500
520     | Put  | 2.10   | 2.18   | 17.8%  | -0.38  | 38,100  | 9,800
525     | Call | 1.22   | 1.28   | 19.1%  | 0.38   | 52,400  | 15,200
525     | Put  | 4.85   | 4.93   | 18.5%  | -0.62  | 29,600  | 7,300

Notable Strikes:
  Highest OI: 525 Call (52,400) — potential resistance
  Highest Volume: 525 Call (15,200) — unusual activity today
  IV Skew: Puts trading 0.4% higher IV than calls at 520 strike

FMV Comparison (where mark ≠ mid by >5%):
  520 Call: Mid $3.485, FMV $3.52 (+1.0%) — fair
  525 Put:  Mid $4.890, FMV $5.05 (+3.3%) — slight underpricing
```

### 5. Raw data mode

When the user requests "raw options data", output the full JSON response with minimal formatting. This is for piping into notebooks, scripts, or future ML pipelines:

```json
{
  "symbol": "SPY",
  "data": [
    {
      "contractID": "SPY260509C00520000",
      "symbol": "SPY",
      "expiration": "2026-05-09",
      "strike": "520.00",
      "type": "call",
      "last": "3.50",
      "bid": "3.45",
      "ask": "3.52",
      "volume": "12500",
      "open_interest": "45230",
      "implied_volatility": "0.182",
      "delta": "0.62",
      "gamma": "0.045",
      "theta": "-0.12",
      "vega": "0.28",
      "rho": "0.03"
    }
  ]
}
```

### 6. Historical mode

For historical queries, show the same table structure but note the date:

```
📋 SPY Historical Options — 2026-04-30

(same table format as realtime, with a note that these are end-of-day values)
```
```

- [ ] **Step 2: Commit**

```bash
git add .claude/skills/options-data/SKILL.md
git commit -m "feat(skill): add options-data standalone skill (SKILL.md-only)"
```

---

### Task 10: Update `CLAUDE.md`

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update the Status section**

Add the new skills to the status list:

```markdown
- ✅ `equity-fundamentals` — standalone SKILL.md, AlphaVantage MCP direct (company overview, financials, earnings)
- ✅ `insider-institutional` — standalone SKILL.md, AlphaVantage MCP direct (insider transactions, institutional holdings)
- ✅ `options-data` — standalone SKILL.md, AlphaVantage MCP direct (realtime/historical chains + Greeks + FMV)
- ✅ `session-news-brief` gains AlphaVantage macro context, NLP sentiment enrichment, and top movers sections
```

- [ ] **Step 2: Update the Layout section**

Add new files to the layout tree:

Under `src/trading_agent_skills/`:
```
  macro_context.py     # AV economic indicators → direction + staleness
  av_sentiment.py      # AV NEWS_SENTIMENT → article enrichment + 4th source
```

Under `.claude/skills/`:
```
  equity-fundamentals/SKILL.md
  insider-institutional/SKILL.md
  options-data/SKILL.md
```

- [ ] **Step 3: Add AlphaVantage MCP note**

Add to the prerequisites/API keys section:

```markdown
**AlphaVantage MCP** is configured at the MCP server level (API key in the server config, not in env vars or code). When the server is absent, all AV-dependent features degrade gracefully — the news brief runs without macro context, sentiment, or top movers; standalone skills (fundamentals, insider, options) instruct the user to configure the server.
```

- [ ] **Step 4: Update test count**

Update the test count from `443` to the new total (expected: ~472).

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document AlphaVantage MCP skills in CLAUDE.md"
```

---

## Dependency Graph

```
Task 1 (NewsArticle fields) ─────────┐
                                      ├──→ Task 3 (av_sentiment.py) ──┐
Task 2 (macro_context.py) ───────────┤                                ├──→ Task 5 (CLI) ──→ Task 6 (SKILL.md update)
                                      └──→ Task 4 (news_brief.py) ───┘
Task 7 (equity-fundamentals SKILL.md)   ← independent
Task 8 (insider-institutional SKILL.md) ← independent
Task 9 (options-data SKILL.md)          ← independent
Task 10 (CLAUDE.md)                     ← after all others
```

Tasks 1, 2, 7, 8, 9 can run in parallel. Task 3 depends on 1. Task 4 depends on 2. Task 5 depends on 3+4. Tasks 6 depends on 5. Task 10 is last.
