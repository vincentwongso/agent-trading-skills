"""Session news brief orchestrator — pure function over pre-fetched data.

The agent assembles MCP outputs (positions, bars, symbol meta) plus Calix
calendar payloads plus news fan-out results from the three providers, hands
the bundle to this orchestrator, and renders the result.

Sections produced:
  - **Calendar overlay**: high-impact economic + earnings events within the
    lookahead window, grouped by impacted watchlist symbol
  - **News by symbol**: deduped articles relevant to each watchlist symbol,
    sorted by recency
  - **Swing candidates**: symbols sitting at an RSI extreme that pay positive
    carry on the side that aligns with mean-reversion (UKOIL-style setups)
  - **Health**: per-provider statuses + Calix degraded flags

This module is pure — no I/O, no broker calls, no httpx. The agent fetches.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional

from trading_agent_skills.checklist import CalixEarningsEntry, CalixEconomicEvent
from trading_agent_skills.indicators import Bar, InsufficientBars, snapshot
from trading_agent_skills.news_dedup import (
    ClusteredArticle,
    NewsArticle,
    dedupe_articles,
)
from trading_agent_skills.symbol_meta import (
    _EARNINGS_RELEVANT_INDICES,
    constituents_of,
    currencies_of_interest,
    topic_vocab_for,
)
from trading_agent_skills.watchlist import WatchlistResolution


DEFAULT_CALENDAR_LOOKAHEAD_HOURS = 4
DEFAULT_NEWS_LOOKBACK_HOURS = 12
RSI_OVERSOLD = Decimal("30")
RSI_OVERBOUGHT = Decimal("70")


# ---------- Inputs ---------------------------------------------------------


@dataclass(frozen=True)
class SymbolMeta:
    symbol: str
    currency_base: str
    currency_profit: str
    category: str          # "forex" | "metals" | "indices" | "crypto" | ...
    swap_long: Decimal
    swap_short: Decimal


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


# ---------- Outputs --------------------------------------------------------


@dataclass(frozen=True)
class CalendarItem:
    kind: str              # "economic" | "earnings"
    title: str
    when_utc: str
    impact: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NewsItem:
    title: str
    publisher: str
    sources: tuple[str, ...]
    url: str
    published_at_utc: str
    impact: str
    summary: str

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
        )


@dataclass(frozen=True)
class SwingCandidate:
    symbol: str
    direction: str         # "long_carry" | "short_carry"
    rsi_14: Decimal
    atr_14: Decimal
    atr_pct_of_price: Decimal
    distance_from_ema_atr_units: Decimal
    swap_long: Decimal
    swap_short: Decimal
    last_close: Decimal
    thesis: str


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


# ---------- Helpers --------------------------------------------------------


def _ensure_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _is_earnings_relevant_index(symbol: str) -> bool:
    """True for stock indices where constituent earnings move price.
    False for commodities even though they're in ``_INDEX_TO_CURRENCIES``."""
    return symbol.upper() in _EARNINGS_RELEVANT_INDICES


def _symbol_currencies(meta: SymbolMeta) -> set[str]:
    return currencies_of_interest(
        symbol=meta.symbol,
        currency_base=meta.currency_base,
        currency_profit=meta.currency_profit,
        category=meta.category,
    )


def _build_calendar_overlay(
    *,
    watchlist: Iterable[str],
    symbol_meta: Mapping[str, SymbolMeta],
    economic_events: list[CalixEconomicEvent],
    earnings_entries: list[CalixEarningsEntry],
    now_utc: datetime,
    lookahead_hours: int,
) -> dict[str, list[CalendarItem]]:
    horizon = now_utc + timedelta(hours=lookahead_hours)
    out: dict[str, list[CalendarItem]] = {}
    today_iso = now_utc.date().isoformat()

    for sym in watchlist:
        meta = symbol_meta.get(sym)
        if meta is None:
            continue
        relevant_currencies = _symbol_currencies(meta)
        items: list[CalendarItem] = []

        for evt in economic_events:
            if evt.currency in relevant_currencies and now_utc <= evt.scheduled_at_utc <= horizon:
                items.append(CalendarItem(
                    kind="economic",
                    title=evt.title,
                    when_utc=evt.scheduled_at_utc.isoformat(),
                    impact=evt.impact,
                    detail={"currency": evt.currency},
                ))

        if _is_earnings_relevant_index(sym):
            for ent in earnings_entries:
                if ent.scheduled_date == today_iso:
                    items.append(CalendarItem(
                        kind="earnings",
                        title=f"{ent.symbol} earnings ({ent.timing})",
                        when_utc=f"{ent.scheduled_date}T00:00:00+00:00",
                        impact="medium",
                        detail={"constituent": ent.symbol, "timing": ent.timing},
                    ))

        if items:
            items.sort(key=lambda i: i.when_utc)
            out[sym] = items
    return out


def _is_currency_pair_tag(tag: str) -> bool:
    """True for ``XAU-USD`` / ``XAU/USD`` / ``XAUUSD`` style pair tags.

    Distinguishes pair-form article tags from bare ISO currency codes
    (``USD``). Pair tags must align by full pair to match a symbol; bare
    codes are macro-relevant to any symbol with that currency in scope.
    """
    if "-" in tag or "/" in tag:
        return True
    # Six-letter all-alpha string (e.g. ``EURUSD``) is a pair without separator.
    return len(tag) == 6 and tag.isalpha()


def _canonicalize_pair(tag: str) -> str:
    """Normalize ``XAU-USD`` / ``XAU/USD`` / ``XAUUSD`` → ``XAUUSD``."""
    return tag.replace("-", "").replace("/", "").upper()


def _topic_vocab_pattern(vocab: Iterable[str]) -> re.Pattern[str]:
    return re.compile(
        r"\b(?:" + "|".join(re.escape(t) for t in vocab) + r")\b",
        re.IGNORECASE,
    )


def _articles_relevant_to(
    sym: str,
    meta: Optional[SymbolMeta],
    article: NewsArticle,
) -> bool:
    """Decide whether ``article`` is relevant to watchlist symbol ``sym``.

    Match order (most specific → most general):

    1. Explicit symbol membership (broker form, e.g. ``XAUUSD.z``).
    2. Canonical pair match — article tagged ``XAU-USD`` ↔ symbol ``XAUUSD``
       (or ``XAUUSD.z`` after stripping the broker suffix).
    3. Index-constituent equity ticker — article tagged ``('AAPL',)`` →
       NAS100 when ``meta.category == "indices"`` and AAPL is in
       :data:`symbol_meta._INDEX_CONSTITUENTS`.
    4. Bare-currency-tag intersection — article tagged ``('USD',)`` (Fed
       statement etc.) is macro-relevant to any USD-quoted symbol. Pair
       tags like ``XAU-USD`` are excluded here so they only match via
       step 2, never via the shared USD leg.
    5. Article-keyword intersection with the symbol's currencies of
       interest (forex pairs primarily).
    6. Topic-vocabulary regex on title + summary (commodities + metals
       only) — recovers Finnhub general-feed articles whose tags are empty.

    Deliberately removed: the prior "currency code in title/summary"
    substring fallback. ``"USD"`` appears in nearly every forex headline
    (``XAU/USD``, ``USD/JPY``) and caused every USD-quoted symbol to
    inherit every USD-tagged article — the Bug #3 phantom-dedup pattern.
    """
    upper_sym = sym.upper()
    base_sym = upper_sym.split(".")[0]
    article_symbols = {s.upper() for s in article.symbols}

    # 1. Explicit symbol membership — broker form (``XAUUSD.z``) or plain.
    if upper_sym in article_symbols or base_sym in article_symbols:
        return True

    # 2. Canonical pair match — strip dash/slash from article pair tags so
    #    ``XAU-USD`` aligns with ``XAUUSD`` / ``XAUUSD.z``.
    article_pair_canon = {
        _canonicalize_pair(s) for s in article_symbols if _is_currency_pair_tag(s)
    }
    if base_sym in article_pair_canon:
        return True

    if meta is None:
        return False

    # 3. Index-constituent equity ticker — Marketaux-style ``('AAPL',)``
    #    tagging routes to NAS100 when the symbol is an index.
    if meta.category.lower() == "indices":
        constituents = constituents_of(base_sym)
        if article_symbols & constituents:
            return True

    relevant = {c.upper() for c in _symbol_currencies(meta)}
    if relevant:
        # 4. Bare-currency tags only — pair tags excluded (handled in step 2).
        bare_currency_tags = {
            s for s in article_symbols if not _is_currency_pair_tag(s)
        }
        if bare_currency_tags & relevant:
            return True

        # 5. Article keywords — forex providers often expose currency codes
        #    here rather than in symbols.
        article_kw = {k.upper() for k in article.keywords}
        if article_kw & relevant:
            return True

    # 6. Topic vocabulary — word-bounded so substrings like ``"toiling"`` or
    #    ``"marigolds"`` don't trigger a false positive.
    vocab = topic_vocab_for(base_sym)
    if vocab:
        haystack = article.title + " " + article.summary
        if _topic_vocab_pattern(vocab).search(haystack):
            return True

    return False


def _build_news_by_symbol(
    *,
    watchlist: Iterable[str],
    symbol_meta: Mapping[str, SymbolMeta],
    articles_by_provider: Mapping[str, list[NewsArticle]],
    now_utc: datetime,
    lookback_hours: int,
) -> tuple[dict[str, list[NewsItem]], list[ClusteredArticle]]:
    cutoff = now_utc - timedelta(hours=lookback_hours)
    pool: list[NewsArticle] = []
    for arts in articles_by_provider.values():
        for a in arts:
            if _ensure_utc(a.published_at_utc) >= cutoff:
                pool.append(a)
    clusters = dedupe_articles(pool)
    # Sort by primary published_at_utc desc.
    clusters_sorted = sorted(
        clusters,
        key=lambda c: c.primary.published_at_utc,
        reverse=True,
    )

    out: dict[str, list[NewsItem]] = {}
    for sym in watchlist:
        meta = symbol_meta.get(sym)
        relevant: list[NewsItem] = []
        for cluster in clusters_sorted:
            if any(
                _articles_relevant_to(sym, meta, art)
                for art in cluster.all_articles
            ):
                relevant.append(NewsItem.from_cluster(cluster))
        if relevant:
            out[sym] = relevant
    return out, clusters_sorted


def _build_swing_candidates(
    *,
    watchlist: Iterable[str],
    bars_by_symbol: Mapping[str, list[Bar]],
    symbol_meta: Mapping[str, SymbolMeta],
) -> tuple[list[SwingCandidate], list[str], list[str]]:
    """Returns (candidates, missing_data, insufficient_bars).

    ``missing_data`` is symbols where the orchestrator didn't supply meta
    or bars at all — that's a watchlist-resolution gap, not a math problem.
    ``insufficient_bars`` is symbols where the bar series was present but
    too short for ATR(14)/RSI(14)/EMA(20) — that's the only case worth
    flagging as ``INDICATOR_DATA_INSUFFICIENT`` to the user.
    """
    candidates: list[SwingCandidate] = []
    missing_data: list[str] = []
    insufficient_bars: list[str] = []
    for sym in watchlist:
        meta = symbol_meta.get(sym)
        bars = bars_by_symbol.get(sym, [])
        if meta is None or not bars:
            missing_data.append(sym)
            continue
        try:
            snap = snapshot(sym, bars)
        except InsufficientBars:
            insufficient_bars.append(sym)
            continue

        if snap.rsi_14 < RSI_OVERSOLD and meta.swap_long > 0:
            thesis = (
                f"{sym}: D1 RSI {snap.rsi_14:.1f}, oversold; long pays "
                f"{meta.swap_long}/lot/night. Mean-reversion bounce on a "
                "positive-carry side — verify fundamentals before entry."
            )
            candidates.append(SwingCandidate(
                symbol=sym,
                direction="long_carry",
                rsi_14=snap.rsi_14,
                atr_14=snap.atr_14,
                atr_pct_of_price=snap.atr_pct_of_price,
                distance_from_ema_atr_units=snap.distance_from_ema_atr_units,
                swap_long=meta.swap_long,
                swap_short=meta.swap_short,
                last_close=snap.last_close,
                thesis=thesis,
            ))
        elif snap.rsi_14 > RSI_OVERBOUGHT and meta.swap_short > 0:
            thesis = (
                f"{sym}: D1 RSI {snap.rsi_14:.1f}, overbought; short pays "
                f"{meta.swap_short}/lot/night. Mean-reversion fade on a "
                "positive-carry side — verify fundamentals before entry."
            )
            candidates.append(SwingCandidate(
                symbol=sym,
                direction="short_carry",
                rsi_14=snap.rsi_14,
                atr_14=snap.atr_14,
                atr_pct_of_price=snap.atr_pct_of_price,
                distance_from_ema_atr_units=snap.distance_from_ema_atr_units,
                swap_long=meta.swap_long,
                swap_short=meta.swap_short,
                last_close=snap.last_close,
                thesis=thesis,
            ))
    return candidates, missing_data, insufficient_bars


def _build_health(
    provider_status: Mapping[str, str],
    economic_stale: bool,
    earnings_stale: bool,
) -> dict[str, str]:
    health: dict[str, str] = {}
    for name, status in provider_status.items():
        health[name] = status
    health["calix_economic"] = "stale" if economic_stale else "ok"
    health["calix_earnings"] = "stale" if earnings_stale else "ok"
    return health


# ---------- Top-level ------------------------------------------------------


def build(inp: NewsBriefInput) -> NewsBriefResult:
    """Compose the brief from pre-fetched data."""
    now_utc = _ensure_utc(inp.now_utc)
    flags: list[str] = []
    notes: list[str] = []

    calendar_by_symbol = _build_calendar_overlay(
        watchlist=inp.watchlist.symbols,
        symbol_meta=inp.symbol_meta,
        economic_events=inp.economic_events,
        earnings_entries=inp.earnings_entries,
        now_utc=now_utc,
        lookahead_hours=inp.lookahead_hours,
    )

    news_by_symbol, _all_clusters = _build_news_by_symbol(
        watchlist=inp.watchlist.symbols,
        symbol_meta=inp.symbol_meta,
        articles_by_provider=inp.articles_by_provider,
        now_utc=now_utc,
        lookback_hours=inp.lookback_hours,
    )

    swing_candidates, missing_data, insufficient_bars = _build_swing_candidates(
        watchlist=inp.watchlist.symbols,
        bars_by_symbol=inp.bars_by_symbol,
        symbol_meta=inp.symbol_meta,
    )
    if insufficient_bars:
        flags.append("INDICATOR_DATA_INSUFFICIENT")
        notes.append(
            "Indicators skipped (bar series too short) for: "
            + ", ".join(insufficient_bars)
            + " — need ≥21 D1 bars."
        )
    if missing_data:
        # No flag — this is an orchestrator/watchlist gap, not a data quality
        # signal. Surface it as a note so the user can see why a symbol
        # silently dropped out of the swing-candidates evaluation.
        notes.append(
            "No bars or symbol meta supplied for: "
            + ", ".join(missing_data)
            + " — those symbols are excluded from swing-candidate evaluation."
        )

    health = _build_health(
        inp.provider_status, inp.economic_stale, inp.earnings_stale
    )
    if any(s == "no_api_key" for s in inp.provider_status.values()):
        flags.append("MISSING_NEWS_API_KEY")
    if any(
        s.startswith("http_") or s in ("unavailable", "schema_error")
        for s in inp.provider_status.values()
    ):
        flags.append("NEWS_PROVIDER_DEGRADED")
    if inp.economic_stale or inp.earnings_stale:
        flags.append("CALIX_DEGRADED")
        notes.append(
            "Calix calendar data is stale; treat news-proximity calls in the "
            "checklist with extra caution."
        )

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
    )


__all__ = [
    "DEFAULT_CALENDAR_LOOKAHEAD_HOURS",
    "DEFAULT_NEWS_LOOKBACK_HOURS",
    "RSI_OVERSOLD",
    "RSI_OVERBOUGHT",
    "SymbolMeta",
    "NewsBriefInput",
    "CalendarItem",
    "NewsItem",
    "SwingCandidate",
    "NewsBriefResult",
    "build",
]
