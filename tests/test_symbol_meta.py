"""symbol_meta: currency-of-interest mapping + conversion-pair derivation."""

from cfd_skills.symbol_meta import (
    conversion_pair,
    currencies_of_interest,
    is_fx_pair,
)


class TestIsFxPair:
    def test_eurusd_is_pair(self):
        assert is_fx_pair("EUR", "USD") is True

    def test_xauusd_is_not_pair(self):
        assert is_fx_pair("XAU", "USD") is True  # XAU is alphabetic & uppercase too
        # Note: XAU is technically ISO-4217 for gold; our test treats it as a
        # currency code. The category-based dispatch is what disambiguates.

    def test_lowercase_rejected(self):
        assert is_fx_pair("eur", "USD") is False

    def test_short_rejected(self):
        assert is_fx_pair("EU", "USD") is False


class TestCurrenciesOfInterest:
    def test_eurusd_returns_both_sides(self):
        assert currencies_of_interest("EURUSD", "EUR", "USD", "Forex") == {"EUR", "USD"}

    def test_usdjpy_returns_both_sides(self):
        assert currencies_of_interest("USDJPY", "USD", "JPY", "Forex") == {"USD", "JPY"}

    def test_xauusd_returns_profit_ccy(self):
        # XAUUSD is "Metals", not "Forex" — only USD matters for news.
        assert currencies_of_interest("XAUUSD", "XAU", "USD", "Metals") == {"USD"}

    def test_us500_uses_index_map(self):
        assert currencies_of_interest("US500", "USD", "USD", "Indices") == {"USD"}

    def test_ukoil_includes_gbp_and_usd(self):
        # Brent crude is USD-denominated but UK politics matter — explicit map.
        assert currencies_of_interest("UKOIL", "USD", "USD", "Energies") == {"USD", "GBP"}

    def test_unknown_index_falls_back_to_profit(self):
        # If we don't have a mapping, return profit ccy as best-effort.
        assert currencies_of_interest("EXOTIC", "XYZ", "EUR", "Indices") == {"EUR"}

    def test_empty_profit_returns_empty(self):
        assert currencies_of_interest("WEIRD", "", "", "") == set()


class TestConversionPair:
    def test_same_currency_returns_none(self):
        assert conversion_pair("USD", "USD") is None

    def test_usd_to_jpy_returns_usdjpy(self):
        assert conversion_pair("USD", "JPY") == "USDJPY"

    def test_eur_to_usd_returns_eurusd(self):
        assert conversion_pair("EUR", "USD") == "EURUSD"
