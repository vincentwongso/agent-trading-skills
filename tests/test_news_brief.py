"""Tests for ``trading_agent_skills.news_brief.build``."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading_agent_skills.checklist import CalixEarningsEntry, CalixEconomicEvent
from trading_agent_skills.indicators import Bar
from trading_agent_skills.news_brief import (
    NewsBriefInput,
    SymbolMeta,
    build,
)
from trading_agent_skills.news_dedup import NewsArticle, canonicalise_url
from trading_agent_skills.watchlist import resolve_watchlist


# ---------- Builders --------------------------------------------------------


def _bar_series(
    symbol: str, closes: list[str], hl_spread: str = "1.0"
) -> list[Bar]:
    base = datetime(2026, 4, 1, tzinfo=timezone.utc)
    spread = Decimal(hl_spread)
    out = []
    for i, c in enumerate(closes):
        cd = Decimal(c)
        out.append(Bar(
            time_utc=base + timedelta(days=i),
            open=cd, high=cd + spread, low=cd - spread, close=cd, volume=0,
        ))
    return out


def _meta(
    symbol: str,
    *,
    base: str,
    profit: str,
    category: str,
    swap_long: str = "0",
    swap_short: str = "0",
) -> SymbolMeta:
    return SymbolMeta(
        symbol=symbol,
        currency_base=base,
        currency_profit=profit,
        category=category,
        swap_long=Decimal(swap_long),
        swap_short=Decimal(swap_short),
    )


def _article(
    *,
    title: str,
    url: str = "https://reuters.com/x",
    publisher: str = "Reuters",
    source: str = "finnhub",
    published: datetime | None = None,
    symbols: tuple[str, ...] = (),
    keywords: tuple[str, ...] = (),
) -> NewsArticle:
    return NewsArticle(
        title=title,
        summary="",
        url=url,
        canonical_url=canonicalise_url(url),
        published_at_utc=published or datetime(2026, 4, 29, 12, 0, tzinfo=timezone.utc),
        source=source,
        publisher=publisher,
        symbols=symbols,
        keywords=keywords,
        impact="low",
    )


def _input(
    *,
    watchlist: tuple[str, ...] = ("XAUUSD",),
    symbol_meta: dict | None = None,
    bars_by_symbol: dict | None = None,
    economic_events: list | None = None,
    earnings_entries: list | None = None,
    economic_stale: bool = False,
    earnings_stale: bool = False,
    articles_by_provider: dict | None = None,
    provider_status: dict | None = None,
    now: datetime | None = None,
) -> NewsBriefInput:
    res = resolve_watchlist(explicit=watchlist, default=watchlist)
    return NewsBriefInput(
        now_utc=now or datetime(2026, 4, 29, 21, 0, tzinfo=timezone.utc),
        lookahead_hours=4,
        lookback_hours=12,
        watchlist=res,
        bars_by_symbol=bars_by_symbol or {},
        symbol_meta=symbol_meta or {},
        economic_events=economic_events or [],
        earnings_entries=earnings_entries or [],
        economic_stale=economic_stale,
        earnings_stale=earnings_stale,
        articles_by_provider=articles_by_provider or {},
        provider_status=provider_status or {},
    )


# ---------- Calendar overlay -----------------------------------------------


def test_calendar_overlay_groups_event_to_relevant_symbol() -> None:
    now = datetime(2026, 4, 29, 21, 0, tzinfo=timezone.utc)
    evt = CalixEconomicEvent(
        title="FOMC Statement",
        currency="USD", impact="High",
        scheduled_at_utc=now + timedelta(hours=2),
    )
    result = build(_input(
        watchlist=("XAUUSD", "USDJPY"),
        symbol_meta={
            "XAUUSD": _meta("XAUUSD", base="XAU", profit="USD", category="metals"),
            "USDJPY": _meta("USDJPY", base="USD", profit="JPY", category="forex"),
        },
        economic_events=[evt],
        now=now,
    ))
    # Both symbols have USD exposure → both get the event.
    assert "XAUUSD" in result.calendar_by_symbol
    assert "USDJPY" in result.calendar_by_symbol
    assert result.calendar_by_symbol["XAUUSD"][0].title == "FOMC Statement"


def test_calendar_overlay_skips_outside_lookahead() -> None:
    now = datetime(2026, 4, 29, 21, 0, tzinfo=timezone.utc)
    evt = CalixEconomicEvent(
        title="Way Too Late",
        currency="USD", impact="High",
        scheduled_at_utc=now + timedelta(hours=10),
    )
    result = build(_input(
        watchlist=("XAUUSD",),
        symbol_meta={
            "XAUUSD": _meta("XAUUSD", base="XAU", profit="USD", category="metals"),
        },
        economic_events=[evt],
        now=now,
    ))
    assert result.calendar_by_symbol == {}


def test_calendar_overlay_attaches_index_earnings() -> None:
    now = datetime(2026, 4, 29, 21, 0, tzinfo=timezone.utc)
    earnings = [
        CalixEarningsEntry(symbol="AAPL", scheduled_date="2026-04-29", timing="amc"),
    ]
    result = build(_input(
        watchlist=("NAS100",),
        symbol_meta={
            "NAS100": _meta("NAS100", base="USD", profit="USD", category="indices"),
        },
        earnings_entries=earnings,
        now=now,
    ))
    assert "NAS100" in result.calendar_by_symbol
    assert any(i.kind == "earnings" for i in result.calendar_by_symbol["NAS100"])


def test_calendar_overlay_skips_earnings_for_non_index() -> None:
    now = datetime(2026, 4, 29, 21, 0, tzinfo=timezone.utc)
    earnings = [
        CalixEarningsEntry(symbol="AAPL", scheduled_date="2026-04-29", timing="amc"),
    ]
    result = build(_input(
        watchlist=("XAUUSD",),
        symbol_meta={
            "XAUUSD": _meta("XAUUSD", base="XAU", profit="USD", category="metals"),
        },
        earnings_entries=earnings,
        now=now,
    ))
    assert "XAUUSD" not in result.calendar_by_symbol or not any(
        i.kind == "earnings" for i in result.calendar_by_symbol.get("XAUUSD", [])
    )


def test_calendar_overlay_skips_earnings_for_commodity_cfds() -> None:
    """USOIL/UKOIL appear in ``_INDEX_TO_CURRENCIES`` for currency-event
    dispatch but are not stock indices — constituent earnings (MSFT, AAPL...)
    don't move oil prices, so they must not attach. Regression guard for the
    smoke-test bug where USOIL got 4 spurious earnings entries."""
    now = datetime(2026, 4, 29, 21, 0, tzinfo=timezone.utc)
    earnings = [
        CalixEarningsEntry(symbol="MSFT", scheduled_date="2026-04-29", timing="amc"),
        CalixEarningsEntry(symbol="AAPL", scheduled_date="2026-04-29", timing="amc"),
    ]
    result = build(_input(
        watchlist=("USOIL", "UKOIL", "NAS100"),
        symbol_meta={
            "USOIL": _meta("USOIL", base="USOIL", profit="USD", category="commodities"),
            "UKOIL": _meta("UKOIL", base="UKOIL", profit="USD", category="commodities"),
            "NAS100": _meta("NAS100", base="NAS100", profit="USD", category="indices"),
        },
        earnings_entries=earnings,
        now=now,
    ))
    for commodity in ("USOIL", "UKOIL"):
        assert not any(
            i.kind == "earnings"
            for i in result.calendar_by_symbol.get(commodity, [])
        ), f"{commodity} should not get earnings"
    # NAS100 (a stock index) is the only one that should pick them up.
    assert any(
        i.kind == "earnings"
        for i in result.calendar_by_symbol.get("NAS100", [])
    )


# ---------- News by symbol -------------------------------------------------


def test_news_grouped_by_symbol_via_ticker_match() -> None:
    art = _article(
        title="Apple beats earnings",
        symbols=("AAPL",),
        published=datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc),
    )
    result = build(_input(
        watchlist=("AAPL",),
        symbol_meta={
            "AAPL": _meta("AAPL", base="AAPL", profit="USD", category="stocks"),
        },
        articles_by_provider={"finnhub": [art]},
        provider_status={"finnhub": "ok"},
    ))
    assert "AAPL" in result.news_by_symbol


def test_news_grouped_via_currency_keyword_for_forex() -> None:
    art = _article(
        title="ECB hints at rate cut",
        keywords=("EUR", "USD"),
        symbols=(),  # forexnews-style: tags currencies, not tickers
        source="forexnews",
        published=datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc),
    )
    result = build(_input(
        watchlist=("EURUSD",),
        symbol_meta={
            "EURUSD": _meta("EURUSD", base="EUR", profit="USD", category="forex"),
        },
        articles_by_provider={"forexnews": [art]},
        provider_status={"forexnews": "ok"},
    ))
    assert "EURUSD" in result.news_by_symbol


def test_news_dedupes_across_providers() -> None:
    when = datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc)
    a = _article(title="Fed holds rates", url="https://reuters.com/x",
                  publisher="Reuters", source="finnhub",
                  symbols=("USD",), published=when)
    b = _article(title="Fed Holds Rates", url="https://yahoo.com/x",
                  publisher="Yahoo", source="marketaux",
                  symbols=("USD",), published=when)
    result = build(_input(
        watchlist=("XAUUSD",),
        symbol_meta={
            "XAUUSD": _meta("XAUUSD", base="XAU", profit="USD", category="metals"),
        },
        articles_by_provider={"finnhub": [a], "marketaux": [b]},
        provider_status={"finnhub": "ok", "marketaux": "ok"},
    ))
    items = result.news_by_symbol.get("XAUUSD", [])
    assert len(items) == 1
    assert len(items[0].sources) == 2


def test_news_filters_outside_lookback_window() -> None:
    too_old = datetime(2026, 4, 28, 0, 0, tzinfo=timezone.utc)  # 45h ago
    art = _article(title="Stale story", symbols=("USD",), published=too_old)
    result = build(_input(
        watchlist=("XAUUSD",),
        symbol_meta={
            "XAUUSD": _meta("XAUUSD", base="XAU", profit="USD", category="metals"),
        },
        articles_by_provider={"finnhub": [art]},
        provider_status={"finnhub": "ok"},
        now=datetime(2026, 4, 29, 21, 0, tzinfo=timezone.utc),
    ))
    assert "XAUUSD" not in result.news_by_symbol


# ---------- Swing candidates -----------------------------------------------


def test_swing_candidate_identifies_oversold_positive_long_carry() -> None:
    """UKOIL-style: long downtrend (RSI < 30) + positive long swap."""
    closes = [str(80 - i * 0.5) for i in range(25)]  # downtrend
    bars = _bar_series("UKOIL", closes)
    result = build(_input(
        watchlist=("UKOIL",),
        symbol_meta={
            "UKOIL": _meta(
                "UKOIL", base="UKOIL", profit="USD", category="commodities",
                swap_long="125", swap_short="-150",
            ),
        },
        bars_by_symbol={"UKOIL": bars},
    ))
    assert len(result.swing_candidates) == 1
    sc = result.swing_candidates[0]
    assert sc.direction == "long_carry"
    assert sc.swap_long == Decimal("125")
    assert sc.rsi_14 < Decimal("30")
    assert "oversold" in sc.thesis.lower()


def test_swing_candidate_identifies_overbought_positive_short_carry() -> None:
    closes = [str(100 + i * 0.5) for i in range(25)]  # uptrend
    bars = _bar_series("EURUSD", closes)
    result = build(_input(
        watchlist=("EURUSD",),
        symbol_meta={
            "EURUSD": _meta(
                "EURUSD", base="EUR", profit="USD", category="forex",
                swap_long="-3", swap_short="2",
            ),
        },
        bars_by_symbol={"EURUSD": bars},
    ))
    assert len(result.swing_candidates) == 1
    assert result.swing_candidates[0].direction == "short_carry"


def test_no_swing_when_carry_negative_on_aligned_side() -> None:
    """Oversold with NEGATIVE long swap → not a swing candidate."""
    closes = [str(80 - i * 0.5) for i in range(25)]
    bars = _bar_series("UKOIL", closes)
    result = build(_input(
        watchlist=("UKOIL",),
        symbol_meta={
            "UKOIL": _meta(
                "UKOIL", base="UKOIL", profit="USD", category="commodities",
                swap_long="-50", swap_short="40",
            ),
        },
        bars_by_symbol={"UKOIL": bars},
    ))
    assert result.swing_candidates == []


def test_swing_skipped_when_bars_insufficient() -> None:
    short_bars = _bar_series("UKOIL", [str(80 - i) for i in range(10)])
    result = build(_input(
        watchlist=("UKOIL",),
        symbol_meta={
            "UKOIL": _meta(
                "UKOIL", base="UKOIL", profit="USD", category="commodities",
                swap_long="125", swap_short="-150",
            ),
        },
        bars_by_symbol={"UKOIL": short_bars},
    ))
    assert result.swing_candidates == []
    assert "INDICATOR_DATA_INSUFFICIENT" in result.flags


def test_missing_meta_or_bars_does_not_flag_indicator_insufficient() -> None:
    """If the orchestrator simply didn't supply meta/bars for a watchlist
    symbol, that's a watchlist-resolution gap — not a "bars too short"
    indicator-math problem. Surface it as a note, not as the data-quality
    flag. Regression guard for the smoke-test bug where spurious
    calendar-tier symbols (no bars) tripped the flag globally even though
    the genuinely-watched symbols had >= 21 bars."""
    bars = _bar_series("UKOIL", [str(80 + i) for i in range(25)])
    result = build(_input(
        watchlist=("UKOIL", "PHANTOM"),
        symbol_meta={
            "UKOIL": _meta(
                "UKOIL", base="UKOIL", profit="USD", category="commodities",
                swap_long="125", swap_short="-150",
            ),
            # PHANTOM has no meta/bars in the bundle.
        },
        bars_by_symbol={"UKOIL": bars},
    ))
    assert "INDICATOR_DATA_INSUFFICIENT" not in result.flags
    # The phantom symbol still gets a note so the user sees the gap.
    assert any("PHANTOM" in n for n in result.notes)


# ---------- Health ---------------------------------------------------------


def test_health_summary_per_provider() -> None:
    result = build(_input(
        provider_status={
            "finnhub": "ok",
            "marketaux": "no_api_key",
            "forexnews": "http_503",
        },
    ))
    assert result.health["finnhub"] == "ok"
    assert result.health["marketaux"] == "no_api_key"
    assert result.health["forexnews"] == "http_503"
    assert "MISSING_NEWS_API_KEY" in result.flags
    assert "NEWS_PROVIDER_DEGRADED" in result.flags


def test_calix_stale_flag() -> None:
    result = build(_input(economic_stale=True))
    assert "CALIX_DEGRADED" in result.flags
    assert result.health["calix_economic"] == "stale"


def test_clean_run_has_no_health_flags() -> None:
    result = build(_input(
        provider_status={"finnhub": "ok", "marketaux": "ok"},
    ))
    assert "MISSING_NEWS_API_KEY" not in result.flags
    assert "NEWS_PROVIDER_DEGRADED" not in result.flags
    assert "CALIX_DEGRADED" not in result.flags


# ---------- Top-level structure --------------------------------------------


def test_result_carries_watchlist_metadata() -> None:
    result = build(_input(watchlist=("XAUUSD", "NAS100")))
    assert result.watchlist == ["XAUUSD", "NAS100"]
    assert "explicit" in result.watchlist_by_tier


# ---------- Relevance matching (Bug #3 fix, 2026-05-01) --------------------
#
# Background: round-3 verification showed metals articles cloning to USOIL,
# UKOIL, NAS100 via an over-broad "currency code in title/summary" textual
# fallback. Marketaux equity-tagged articles never reached NAS100. Finnhub
# general-feed articles (empty symbols/keywords) dropped entirely. These
# tests pin the new behavior: explicit symbol → canonical pair → index
# constituent → bare currency / keyword → topic vocab. No substring fallback.


def test_canonical_pair_match_xau_dash_usd_to_xauusd() -> None:
    """ForexNews tags articles 'XAU-USD'; the symbol is 'XAUUSD'. They must
    align without going through the over-broad currency-substring fallback."""
    art = _article(
        title="Gold (XAU/USD) Price Forecast",
        symbols=("XAU-USD",),
        source="forexnews",
        published=datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc),
    )
    result = build(_input(
        watchlist=("XAUUSD",),
        symbol_meta={
            "XAUUSD": _meta("XAUUSD", base="XAU", profit="USD", category="metals"),
        },
        articles_by_provider={"forexnews": [art]},
        provider_status={"forexnews": "ok"},
    ))
    assert "XAUUSD" in result.news_by_symbol


def test_canonical_pair_match_strips_broker_z_suffix() -> None:
    """The Fintrix broker form is 'XAUUSD.z'. Matching must strip the suffix
    so canonical pair 'XAU-USD' attaches to 'XAUUSD.z'."""
    art = _article(
        title="Gold (XAU/USD) Price Forecast",
        symbols=("XAU-USD",),
        source="forexnews",
        published=datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc),
    )
    result = build(_input(
        watchlist=("XAUUSD.z",),
        symbol_meta={
            "XAUUSD.z": _meta(
                "XAUUSD.z", base="XAU", profit="USD", category="metals"
            ),
        },
        articles_by_provider={"forexnews": [art]},
        provider_status={"forexnews": "ok"},
    ))
    assert "XAUUSD.z" in result.news_by_symbol


def test_canonical_pair_does_not_cross_attribute_to_unrelated_symbol() -> None:
    """An article tagged 'XAU-USD' must NOT attach to USOIL or NAS100 just
    because they share the USD leg. This is the exact phantom-dedup pattern
    Bug #3 round-3 surfaced."""
    art = _article(
        title="Gold (XAU/USD) rebounds near monthly low",
        symbols=("XAU-USD",),
        source="forexnews",
        published=datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc),
    )
    result = build(_input(
        watchlist=("XAUUSD", "USOIL", "UKOIL", "NAS100"),
        symbol_meta={
            "XAUUSD": _meta("XAUUSD", base="XAU", profit="USD", category="metals"),
            "USOIL":  _meta("USOIL",  base="USOIL",  profit="USD", category="commodities"),
            "UKOIL":  _meta("UKOIL",  base="UKOIL",  profit="USD", category="commodities"),
            "NAS100": _meta("NAS100", base="NAS100", profit="USD", category="indices"),
        },
        articles_by_provider={"forexnews": [art]},
        provider_status={"forexnews": "ok"},
    ))
    assert "XAUUSD" in result.news_by_symbol
    assert "USOIL"  not in result.news_by_symbol
    assert "UKOIL"  not in result.news_by_symbol
    assert "NAS100" not in result.news_by_symbol


def test_constituent_match_aapl_attaches_to_nas100() -> None:
    """Marketaux tags equity articles with the ticker. NAS100's constituent
    map should route AAPL/MSFT/GOOG/etc. to the index even though no
    currency or pair matches."""
    art = _article(
        title="Apple Q2: Firing On All Cylinders",
        symbols=("AAPL",),
        source="marketaux",
        published=datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc),
    )
    result = build(_input(
        watchlist=("NAS100",),
        symbol_meta={
            "NAS100": _meta("NAS100", base="NAS100", profit="USD", category="indices"),
        },
        articles_by_provider={"marketaux": [art]},
        provider_status={"marketaux": "ok"},
    ))
    assert "NAS100" in result.news_by_symbol


def test_constituent_does_not_match_commodity_or_metal() -> None:
    """('AAPL',) is meaningless for USOIL / XAUUSD. The constituent path is
    indices-only so equity articles don't pollute commodities/metals."""
    art = _article(
        title="Apple Q2: Firing On All Cylinders",
        symbols=("AAPL",),
        source="marketaux",
        published=datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc),
    )
    result = build(_input(
        watchlist=("USOIL", "XAUUSD"),
        symbol_meta={
            "USOIL":  _meta("USOIL",  base="USOIL", profit="USD", category="commodities"),
            "XAUUSD": _meta("XAUUSD", base="XAU",   profit="USD", category="metals"),
        },
        articles_by_provider={"marketaux": [art]},
        provider_status={"marketaux": "ok"},
    ))
    assert "USOIL"  not in result.news_by_symbol
    assert "XAUUSD" not in result.news_by_symbol


def test_topic_vocab_oil_word_matches_usoil_via_finnhub_general_feed() -> None:
    """Finnhub general-feed articles arrive with empty symbols/keywords. A
    word-bounded topic vocab match against title/summary recovers them."""
    art = _article(
        title="Oil retreats after hitting four-year high on US-Iran war fears",
        symbols=(),
        keywords=(),
        source="finnhub",
        published=datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc),
    )
    result = build(_input(
        watchlist=("USOIL",),
        symbol_meta={
            "USOIL": _meta("USOIL", base="USOIL", profit="USD", category="commodities"),
        },
        articles_by_provider={"finnhub": [art]},
        provider_status={"finnhub": "ok"},
    ))
    assert "USOIL" in result.news_by_symbol


def test_topic_vocab_uses_word_boundary_not_substring() -> None:
    """'Toiling' contains the substring 'oil' but is not the OIL topic. The
    matcher must use a word boundary to avoid false positives."""
    art = _article(
        title="Toiling away in the salt mines",
        symbols=(),
        keywords=(),
        source="finnhub",
        published=datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc),
    )
    result = build(_input(
        watchlist=("USOIL",),
        symbol_meta={
            "USOIL": _meta("USOIL", base="USOIL", profit="USD", category="commodities"),
        },
        articles_by_provider={"finnhub": [art]},
        provider_status={"finnhub": "ok"},
    ))
    assert "USOIL" not in result.news_by_symbol


def test_topic_vocab_gold_matches_xauusd_not_usoil() -> None:
    art = _article(
        title="Gold rebounds as dollar dives",
        symbols=(),
        keywords=(),
        source="finnhub",
        published=datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc),
    )
    result = build(_input(
        watchlist=("XAUUSD", "USOIL"),
        symbol_meta={
            "XAUUSD": _meta("XAUUSD", base="XAU",   profit="USD", category="metals"),
            "USOIL":  _meta("USOIL",  base="USOIL", profit="USD", category="commodities"),
        },
        articles_by_provider={"finnhub": [art]},
        provider_status={"finnhub": "ok"},
    ))
    assert "XAUUSD" in result.news_by_symbol
    assert "USOIL"  not in result.news_by_symbol


def test_no_match_when_only_signal_is_currency_substring_in_title() -> None:
    """Regression guard for the dropped fallback: if an article has no
    explicit symbol, no canonical pair, no constituent, no bare-currency
    tag, and no topic vocab hit — the literal substring 'USD' inside
    'XAG/USD' in the title must not by itself attach the article to every
    USD-quoted symbol."""
    art = _article(
        title="Silver Price Forecast: XAG/USD restrictive policy risks",
        symbols=("XAG-USD",),       # canon pair → only XAGUSD should attach
        keywords=(),
        source="forexnews",
        published=datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc),
    )
    result = build(_input(
        watchlist=("XAGUSD", "USOIL", "NAS100"),
        symbol_meta={
            "XAGUSD": _meta("XAGUSD", base="XAG",    profit="USD", category="metals"),
            "USOIL":  _meta("USOIL",  base="USOIL",  profit="USD", category="commodities"),
            "NAS100": _meta("NAS100", base="NAS100", profit="USD", category="indices"),
        },
        articles_by_provider={"forexnews": [art]},
        provider_status={"forexnews": "ok"},
    ))
    assert "XAGUSD" in result.news_by_symbol
    assert "USOIL"  not in result.news_by_symbol
    assert "NAS100" not in result.news_by_symbol


def test_bare_currency_tag_still_matches_macro_event() -> None:
    """Backwards-compat: an article tagged with a bare currency code (Fed
    statement → ('USD',)) is genuinely macro-relevant to every USD-quoted
    symbol. Don't break this — only the over-broad SUBSTRING match was bad,
    not the proper bare-currency-tag intersection."""
    art = _article(
        title="Fed holds rates",
        symbols=("USD",),
        source="finnhub",
        published=datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc),
    )
    result = build(_input(
        watchlist=("XAUUSD", "USOIL", "NAS100"),
        symbol_meta={
            "XAUUSD": _meta("XAUUSD", base="XAU",    profit="USD", category="metals"),
            "USOIL":  _meta("USOIL",  base="USOIL",  profit="USD", category="commodities"),
            "NAS100": _meta("NAS100", base="NAS100", profit="USD", category="indices"),
        },
        articles_by_provider={"finnhub": [art]},
        provider_status={"finnhub": "ok"},
    ))
    assert "XAUUSD" in result.news_by_symbol
    assert "USOIL"  in result.news_by_symbol
    assert "NAS100" in result.news_by_symbol
