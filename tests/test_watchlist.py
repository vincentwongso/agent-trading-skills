"""Tests for ``cfd_skills.watchlist`` — 5-tier resolver."""

from __future__ import annotations

import pytest

from cfd_skills.watchlist import (
    calendar_driven_symbols,
    resolve_watchlist,
    symbols_for_currencies,
)


_DEFAULT = ("XAUUSD", "XAGUSD", "USOIL", "UKOIL", "NAS100")
_BASE_UNIVERSE = (
    "XAUUSD", "XAGUSD", "USOIL", "UKOIL", "NAS100",
    "US500", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD",
)


# ---------- symbols_for_currencies -----------------------------------------


def test_usd_maps_to_usd_symbols() -> None:
    out = symbols_for_currencies(["USD"])
    assert "XAUUSD" in out
    assert "EURUSD" in out


def test_filter_to_base_universe() -> None:
    out = symbols_for_currencies(["USD"], base_universe=_BASE_UNIVERSE)
    assert "USDJPY" in out
    assert "USDCHF" not in out  # not in base universe


def test_unknown_currency_returns_empty() -> None:
    assert symbols_for_currencies(["XYZ"]) == ()


def test_lowercase_currency_normalised() -> None:
    assert "EURUSD" in symbols_for_currencies(["eur"])


def test_filter_matches_broker_suffix_form() -> None:
    """Editorial ``XAUUSD`` matches broker ``XAUUSD.z`` via prefix.
    Returned strings preserve the broker's casing so they round-trip
    through MCP. Regression guard for the smoke-test bug where the
    calendar tier emitted bare ``XAUUSD`` that Fintrix doesn't carry."""
    broker_catalog = ("XAUUSD.z", "XAGUSD.z", "USOIL", "UKOIL", "NAS100")
    out = symbols_for_currencies(["USD"], base_universe=broker_catalog)
    # Suffix-form matches:
    assert "XAUUSD.z" in out
    assert "XAGUSD.z" in out
    # Bare-form also matches (no suffix needed):
    assert "USOIL" in out
    assert "NAS100" in out
    # And nothing not in the broker catalog gets emitted:
    assert "EURUSD" not in out
    assert "EURUSD.z" not in out


# ---------- calendar_driven_symbols ----------------------------------------


def test_calendar_drives_from_event_currencies() -> None:
    out = calendar_driven_symbols(
        economic_event_currencies=["USD"],
        earnings_constituents_for_indices=[],
        base_universe=_BASE_UNIVERSE,
    )
    assert "XAUUSD" in out
    assert "NAS100" in out


def test_calendar_includes_index_when_earnings_flagged() -> None:
    out = calendar_driven_symbols(
        economic_event_currencies=[],
        earnings_constituents_for_indices=["NAS100"],
        base_universe=_BASE_UNIVERSE,
    )
    assert "NAS100" in out


def test_calendar_index_filtered_by_base_universe() -> None:
    out = calendar_driven_symbols(
        economic_event_currencies=[],
        earnings_constituents_for_indices=["GER40"],
        base_universe=_BASE_UNIVERSE,  # GER40 not in this base universe
    )
    assert out == ()


def test_calendar_unknown_index_skipped() -> None:
    out = calendar_driven_symbols(
        economic_event_currencies=[],
        earnings_constituents_for_indices=["MADE_UP"],
    )
    assert out == ()


# ---------- resolve_watchlist ----------------------------------------------


def test_explicit_takes_priority() -> None:
    res = resolve_watchlist(
        explicit=["BTCUSD", "ETHUSD"],
        default=_DEFAULT,
    )
    # Union (deduped, capped) — explicit symbols come first, then default fills.
    assert res.symbols[:2] == ("BTCUSD", "ETHUSD")
    assert res.primary_tier == "explicit"
    assert res.by_tier["explicit"] == ("BTCUSD", "ETHUSD")


def test_open_positions_appended_after_explicit() -> None:
    res = resolve_watchlist(
        explicit=["BTCUSD"],
        open_position_symbols=["XAUUSD", "BTCUSD"],  # BTCUSD already explicit
        default=_DEFAULT,
    )
    assert res.symbols[0] == "BTCUSD"
    assert "XAUUSD" in res.symbols
    assert res.by_tier["open_positions"] == ("XAUUSD",)


def test_calendar_appended_after_positions() -> None:
    res = resolve_watchlist(
        open_position_symbols=["XAUUSD"],
        calendar_symbols=["NAS100", "XAUUSD"],  # XAUUSD dup
        default=_DEFAULT,
    )
    assert res.symbols.index("XAUUSD") == 0
    assert "NAS100" in res.symbols
    assert res.by_tier["calendar"] == ("NAS100",)


def test_volatility_filled_after_calendar() -> None:
    res = resolve_watchlist(
        calendar_symbols=["NAS100"],
        volatility_ranked=["XAGUSD", "BTCUSD"],
        default=_DEFAULT,
    )
    assert "XAGUSD" in res.symbols
    assert res.by_tier["volatility"] == ("XAGUSD", "BTCUSD")


def test_default_used_when_all_others_empty() -> None:
    res = resolve_watchlist(default=_DEFAULT)
    assert res.symbols == _DEFAULT
    assert res.primary_tier == "default"


def test_max_size_caps_total() -> None:
    res = resolve_watchlist(
        explicit=["A", "B", "C"],
        open_position_symbols=["D", "E"],
        calendar_symbols=["F", "G"],
        volatility_ranked=["H", "I", "J"],
        default=("K", "L"),
        max_size=5,
    )
    assert len(res.symbols) == 5
    assert res.symbols[:3] == ("A", "B", "C")


def test_resolve_preserves_input_case_with_case_insensitive_dedup() -> None:
    # Broker symbol form is the source of truth — preserve caller casing.
    res = resolve_watchlist(explicit=["xauusd.z"], default=_DEFAULT)
    assert res.symbols[0] == "xauusd.z"

    # But case-only variants collapse to the first-seen form.
    res = resolve_watchlist(
        explicit=["XAUUSD.z", "xauusd.z", "XAUUSD.Z"],
        default=_DEFAULT,
    )
    assert res.by_tier["explicit"] == ("XAUUSD.z",)


def test_resolve_dedupes_within_a_tier() -> None:
    res = resolve_watchlist(
        explicit=["XAUUSD", "XAUUSD", "USOIL"],
        default=_DEFAULT,
    )
    # The explicit tier itself dedupes XAUUSD, contributing only 2 unique items.
    assert res.by_tier["explicit"] == ("XAUUSD", "USOIL")


def test_resolve_max_size_zero_rejected() -> None:
    with pytest.raises(ValueError):
        resolve_watchlist(default=_DEFAULT, max_size=0)


def test_resolve_default_tier_translates_editorial_to_broker() -> None:
    """Round-2 smoke-test bug: the calendar tier prefix-matched editorial
    ``XAUUSD`` to broker ``XAUUSD.z``, but the default tier passed bare
    ``XAUUSD`` through unchanged. With ``broker_catalog`` provided, the
    default tier should now apply the same prefix-match so the watchlist
    only ever contains symbols the broker actually offers."""
    broker_catalog = ("XAUUSD.z", "XAGUSD.z", "USOIL", "UKOIL", "NAS100")
    res = resolve_watchlist(
        default=_DEFAULT,  # editorial: XAUUSD, XAGUSD, USOIL, UKOIL, NAS100
        broker_catalog=broker_catalog,
    )
    # Editorial → broker translations:
    assert "XAUUSD.z" in res.by_tier["default"]
    assert "XAGUSD.z" in res.by_tier["default"]
    # Symbols already in broker form stay as-is:
    assert "USOIL" in res.by_tier["default"]
    assert "UKOIL" in res.by_tier["default"]
    assert "NAS100" in res.by_tier["default"]
    # And the editorial bare names should NOT leak through:
    assert "XAUUSD" not in res.by_tier["default"]
    assert "XAGUSD" not in res.by_tier["default"]


def test_resolve_default_tier_drops_unmapped_symbols() -> None:
    """If the user's editorial default contains symbols the broker doesn't
    offer (e.g. ``BTCUSD`` on a broker that lacks crypto), translation drops
    them. The user can still see them silently filter out — the orchestrator
    is responsible for surfacing missing data rather than the resolver."""
    broker_catalog = ("XAUUSD.z", "USOIL")
    res = resolve_watchlist(
        default=("XAUUSD", "BTCUSD"),
        broker_catalog=broker_catalog,
    )
    assert res.by_tier["default"] == ("XAUUSD.z",)


def test_resolve_explicit_tier_also_translates() -> None:
    """The explicit tier comes from user input, which may also be editorial.
    Apply the same prefix-match as default tier."""
    broker_catalog = ("XAUUSD.z",)
    res = resolve_watchlist(
        explicit=["XAUUSD"],
        default=(),
        broker_catalog=broker_catalog,
    )
    assert res.by_tier["explicit"] == ("XAUUSD.z",)


def test_resolve_open_positions_pass_through_unchanged() -> None:
    """Open positions come from the broker (``get_positions``) so they're
    already in broker form — no translation needed. With the catalog
    provided, the open_positions tier still receives them verbatim."""
    broker_catalog = ("XAUUSD.z",)
    res = resolve_watchlist(
        open_position_symbols=["XAUUSD.z"],
        default=(),
        broker_catalog=broker_catalog,
    )
    assert res.by_tier["open_positions"] == ("XAUUSD.z",)


def test_resolve_no_catalog_preserves_back_compat() -> None:
    """Without a broker_catalog, all tiers pass through as-is — same as
    pre-fix behaviour. Callers that don't have a catalog still work."""
    res = resolve_watchlist(default=("XAUUSD", "USOIL"))
    assert res.by_tier["default"] == ("XAUUSD", "USOIL")


def test_description_summarises_contributions() -> None:
    res = resolve_watchlist(
        explicit=["BTCUSD"],
        open_position_symbols=["XAUUSD"],
        default=_DEFAULT,
    )
    assert "1 from explicit" in res.description
    assert "1 from open positions" in res.description


def test_empty_inputs_yield_empty_resolution() -> None:
    res = resolve_watchlist(default=())
    assert res.symbols == ()
    assert "empty" in res.description
