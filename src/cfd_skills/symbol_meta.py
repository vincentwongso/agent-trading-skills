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
# currency filters to apply.
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
