"""Symbol parsing and currency-conversion helpers.

Note: mt5-mcp now exposes ``tick_value`` directly in deposit currency on
``SymbolInfo``. Most cash-risk math therefore avoids FX conversion entirely.
The conversion helpers here remain useful for the cases where the skill
needs to reason about *swap currency* (e.g. ``swap_mode == "by_base_currency"``)
or for the news skill's currency-of-interest mapping.
"""

from __future__ import annotations


# Symbols whose impact extends to a currency or commodity not encoded in
# their ticker. Used by news / calendar skills to decide which Calix
# currency filters to apply. Includes both stock indices AND commodities
# because both are sensitive to macro currency events (oil moves on USD,
# UKOIL also on GBP via Brent's London pricing).
_INDEX_TO_CURRENCIES: dict[str, set[str]] = {
    "US500": {"USD"},
    "US30": {"USD"},
    "NAS100": {"USD"},
    "USOIL": {"USD"},
    "UKOIL": {"USD", "GBP"},
    "GER40": {"EUR"},
    "GER30": {"EUR"},
    "UK100": {"GBP"},
    "JPN225": {"JPY"},
    "AUS200": {"AUD"},
    "HK50": {"HKD"},
}


# Subset of the above that are *stock indices* — the only category for which
# constituent earnings (e.g. AAPL, MSFT) drive intraday volatility. Oil
# is excluded: a Microsoft beat doesn't move USOIL.
_EARNINGS_RELEVANT_INDICES: frozenset[str] = frozenset({
    "US500", "US30", "NAS100",
    "GER40", "GER30",
    "UK100",
    "JPN225",
    "AUS200",
    "HK50",
})


# Equity tickers that are large-cap constituents of each stock index. Used by
# the news brief to attach Marketaux equity-tagged articles (`('AAPL',)`) to
# the corresponding index symbol. Indicative top-N by market cap / activity —
# not exhaustive. Vincent's default watchlist only includes NAS100; others are
# stubs that can be expanded when those indices enter the watchlist.
_INDEX_CONSTITUENTS: dict[str, frozenset[str]] = {
    "NAS100": frozenset({
        "AAPL", "MSFT", "GOOG", "GOOGL", "AMZN", "META", "NVDA", "TSLA",
        "AVGO", "AMD", "NFLX", "ADBE", "CSCO", "INTC", "INTU", "QCOM",
        "TXN", "AMAT", "ASML", "MU", "ORCL", "PEP", "COIN", "PYPL",
        "BKNG", "PANW", "SBUX", "MRVL", "ADI", "REGN",
    }),
    "US500": frozenset({
        "AAPL", "MSFT", "GOOG", "GOOGL", "AMZN", "META", "NVDA", "TSLA",
        "AVGO", "JPM", "BAC", "WFC", "XOM", "CVX", "JNJ", "PG", "KO",
        "UNH", "V", "MA", "HD", "WMT", "DIS",
    }),
    "US30": frozenset({
        "AAPL", "MSFT", "JPM", "JNJ", "PG", "KO", "DIS", "BA", "CAT",
        "GS", "HD", "IBM", "MCD", "NKE", "TRV", "UNH", "V", "WMT",
    }),
}


# Topic-keyword vocabularies for symbols whose news arrives untagged in the
# Finnhub general feed. A word-bounded match against title/summary surfaces
# articles like "Oil retreats..." that have empty `symbols`/`keywords` tags.
# Indices use _INDEX_CONSTITUENTS (ticker matching) instead.
_TOPIC_VOCAB: dict[str, frozenset[str]] = {
    "XAUUSD": frozenset({"GOLD", "XAU", "BULLION"}),
    "XAGUSD": frozenset({"SILVER", "XAG"}),
    "USOIL":  frozenset({"OIL", "CRUDE", "WTI", "OPEC", "PETROLEUM"}),
    "UKOIL":  frozenset({"OIL", "CRUDE", "BRENT", "OPEC", "PETROLEUM"}),
}


def constituents_of(symbol: str) -> frozenset[str]:
    """Equity tickers that are constituents of an index symbol.

    Returns an empty frozenset if the symbol is not a known index. Strips a
    Fintrix-style ``.z`` suffix before lookup so ``NAS100.z`` resolves the
    same as ``NAS100``.
    """
    base = symbol.upper().split(".")[0]
    return _INDEX_CONSTITUENTS.get(base, frozenset())


def topic_vocab_for(symbol: str) -> frozenset[str]:
    """Topic-keyword vocabulary for a commodity / metal symbol.

    Returns an empty frozenset for symbols without a configured vocab —
    indices route through ``constituents_of`` instead, FX pairs rely on the
    currency-of-interest path.
    """
    base = symbol.upper().split(".")[0]
    return _TOPIC_VOCAB.get(base, frozenset())


def is_fx_pair(currency_base: str, currency_profit: str) -> bool:
    """True if both sides look like ISO-4217 currency codes (3 uppercase letters)."""
    return (
        len(currency_base) == 3 and currency_base.isalpha() and currency_base.isupper()
        and len(currency_profit) == 3 and currency_profit.isalpha() and currency_profit.isupper()
    )


def currencies_of_interest(
    symbol: str,
    currency_base: str,
    currency_profit: str,
    category: str,
) -> set[str]:
    """Return currencies whose news / calendar events meaningfully move this symbol.

    For FX, both sides of the pair. For metals, the quote currency
    (XAUUSD → USD; gold itself doesn't have a "currency"). For indices,
    the country / region currency mapped via ``_INDEX_TO_CURRENCIES``,
    falling back to the profit currency if unknown.
    """
    sym = symbol.upper()
    if sym in _INDEX_TO_CURRENCIES:
        return _INDEX_TO_CURRENCIES[sym] | {currency_profit}
    if category.lower() == "forex" and is_fx_pair(currency_base, currency_profit):
        return {currency_base, currency_profit}
    # Metals, crypto, stocks fallback: profit currency.
    return {currency_profit} if currency_profit else set()


def conversion_pair(from_ccy: str, to_ccy: str) -> str | None:
    """Symbol name to use for converting `from_ccy` → `to_ccy`, or None if same.

    Returns the *direct* pair (e.g. ``USDJPY``) — the caller may need to
    invert (use 1/rate) if the broker only offers the inverse pair. We
    don't try to be clever about that here; it's the position-sizer's job
    to detect a missing pair and adapt.
    """
    if from_ccy == to_ccy:
        return None
    return f"{from_ccy}{to_ccy}"
