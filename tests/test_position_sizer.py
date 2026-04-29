"""position_sizer: end-to-end sizing including margin, swap, and sanity flags.

Fixtures use realistic mt5-mcp shapes (Decimal-as-string in JSON) so the
``from_mcp`` constructors are exercised on the same path the skill will use.
"""

from decimal import Decimal

import pytest

from cfd_skills.position_sizer import (
    AccountInfo,
    Quote,
    SizingRequest,
    SymbolInfo,
    size,
)


# --- Fixture factories -----------------------------------------------------


def _eurusd_blob(**overrides) -> dict:
    base = {
        "name": "EURUSD",
        "contract_size": "100000",
        "tick_size": "0.00001",
        "tick_value": "1",            # 1 USD per tick per 1 lot (typical FX major)
        "volume_min": "0.01",
        "volume_max": "100",
        "volume_step": "0.01",
        "digits": 5,
        "calc_mode": "forex",
        "swap_mode": "by_points",
        "swap_long": "-2.5",
        "swap_short": "0.8",
        "margin_initial": "0",
        "stops_level": 0,
        "currency_profit": "USD",
        "currency_margin": "EUR",
    }
    base.update(overrides)
    return base


def _usd_account_blob(equity="10000", free_margin="9500", leverage=500) -> dict:
    return {
        "equity": equity,
        "margin_free": free_margin,
        "leverage": leverage,
        "currency": "USD",
    }


def _quote_blob(bid="1.0823", ask="1.0824") -> dict:
    return {"bid": bid, "ask": ask}


def _xauusd_blob(**overrides) -> dict:
    base = {
        "name": "XAUUSD",
        "contract_size": "100",
        "tick_size": "0.01",
        "tick_value": "1",
        "volume_min": "0.01",
        "volume_max": "100",
        "volume_step": "0.01",
        "digits": 2,
        "calc_mode": "cfd_leverage",
        "swap_mode": "by_points",
        "swap_long": "-5",
        "swap_short": "1",
        "margin_initial": "0",
        "stops_level": 0,
        "currency_profit": "USD",
        "currency_margin": "USD",
    }
    base.update(overrides)
    return base


# --- Risk-based sizing -----------------------------------------------------


def test_eurusd_long_1pct_risk_with_20pip_stop():
    # Account 10_000 USD, 1% risk = 100 USD.
    # Stop = 20 pips = 200 points (5-digit broker), tick_value = 1 USD/tick.
    # Cash risk per lot = 200 * 1 = 200 USD.
    # Lot = 100 / 200 = 0.5 lot.
    result = size(
        request=SizingRequest(
            side="long",
            risk_pct=Decimal("1"),
            stop_points=200,
        ),
        account=AccountInfo.from_mcp(_usd_account_blob()),
        quote=Quote.from_mcp(_quote_blob()),
        sym=SymbolInfo.from_mcp(_eurusd_blob()),
    )
    assert result.lot_size == Decimal("0.50")
    assert result.cash_risk == Decimal("100.00")
    assert result.risk_pct_of_equity == Decimal("1.000")


def test_absolute_risk_amount_overrides_pct():
    result = size(
        request=SizingRequest(
            side="long",
            risk_amount=Decimal("50"),
            stop_points=200,
        ),
        account=AccountInfo.from_mcp(_usd_account_blob()),
        quote=Quote.from_mcp(_quote_blob()),
        sym=SymbolInfo.from_mcp(_eurusd_blob()),
    )
    # 50 USD risk / 200 USD per lot = 0.25 lot
    assert result.lot_size == Decimal("0.25")


def test_either_risk_pct_or_amount_required():
    with pytest.raises(ValueError, match="risk_pct or risk_amount"):
        size(
            request=SizingRequest(side="long", stop_points=200),
            account=AccountInfo.from_mcp(_usd_account_blob()),
            quote=Quote.from_mcp(_quote_blob()),
            sym=SymbolInfo.from_mcp(_eurusd_blob()),
        )


def test_either_stop_price_or_points_required():
    with pytest.raises(ValueError, match="stop_price or stop_points"):
        size(
            request=SizingRequest(side="long", risk_pct=Decimal("1")),
            account=AccountInfo.from_mcp(_usd_account_blob()),
            quote=Quote.from_mcp(_quote_blob()),
            sym=SymbolInfo.from_mcp(_eurusd_blob()),
        )


# --- Stop-distance derivation ----------------------------------------------


def test_long_with_explicit_stop_price():
    # entry = ask 1.0824, stop = 1.0804 → 200 points away.
    result = size(
        request=SizingRequest(
            side="long",
            risk_pct=Decimal("1"),
            stop_price=Decimal("1.0804"),
        ),
        account=AccountInfo.from_mcp(_usd_account_blob()),
        quote=Quote.from_mcp(_quote_blob()),
        sym=SymbolInfo.from_mcp(_eurusd_blob()),
    )
    assert result.stop_distance_points == 200
    assert result.stop_price == Decimal("1.0804")


def test_short_with_explicit_stop_price():
    # entry = bid 1.0823, stop = 1.0843 → 200 points above.
    result = size(
        request=SizingRequest(
            side="short",
            risk_pct=Decimal("1"),
            stop_price=Decimal("1.0843"),
        ),
        account=AccountInfo.from_mcp(_usd_account_blob()),
        quote=Quote.from_mcp(_quote_blob()),
        sym=SymbolInfo.from_mcp(_eurusd_blob()),
    )
    assert result.stop_distance_points == 200


# --- Sanity flags ---------------------------------------------------------


def test_below_min_lot_flag():
    # 0.01% risk * 10_000 = 1 USD; 200-pt stop * 1 USD/tick = 200/lot;
    # raw lot = 1/200 = 0.005 → below 0.01 min.
    result = size(
        request=SizingRequest(
            side="long",
            risk_pct=Decimal("0.01"),
            stop_points=200,
        ),
        account=AccountInfo.from_mcp(_usd_account_blob()),
        quote=Quote.from_mcp(_quote_blob()),
        sym=SymbolInfo.from_mcp(_eurusd_blob()),
    )
    assert "BELOW_MIN_LOT" in result.flags


def test_high_margin_usage_flag():
    # Pass a broker_margin so margin_pct_of_free is computable in deposit ccy.
    # Broker says 216 USD margin; free margin 900 USD → 24%. Threshold 10% → flag.
    result = size(
        request=SizingRequest(
            side="long",
            risk_pct=Decimal("1"),
            stop_points=200,
            margin_warning_pct=Decimal("10"),
            broker_margin=Decimal("216"),
        ),
        account=AccountInfo.from_mcp(_usd_account_blob(free_margin="900")),
        quote=Quote.from_mcp(_quote_blob()),
        sym=SymbolInfo.from_mcp(_eurusd_blob()),
    )
    assert "HIGH_MARGIN_USAGE" in result.flags


def test_stop_inside_broker_min_flag():
    # Set broker stops_level = 100 points; user stop = 50 points → flag.
    result = size(
        request=SizingRequest(
            side="long",
            risk_pct=Decimal("1"),
            stop_points=50,
        ),
        account=AccountInfo.from_mcp(_usd_account_blob()),
        quote=Quote.from_mcp(_quote_blob()),
        sym=SymbolInfo.from_mcp(_eurusd_blob(stops_level=100)),
    )
    assert "STOP_INSIDE_BROKER_MIN" in result.flags


def test_stop_tighter_than_2x_spread_flag():
    # Spread = 0.0001 (10 points on 5-digit). 2x = 20. Stop = 15 points → flag.
    result = size(
        request=SizingRequest(
            side="long",
            risk_pct=Decimal("1"),
            stop_points=15,
        ),
        account=AccountInfo.from_mcp(_usd_account_blob()),
        quote=Quote.from_mcp(_quote_blob(bid="1.0823", ask="1.0833")),  # 10pt spread
        sym=SymbolInfo.from_mcp(_eurusd_blob()),
    )
    assert "STOP_TIGHTER_THAN_2X_SPREAD" in result.flags


# --- Margin cross-check ---------------------------------------------------


def test_broker_margin_within_2pct_no_flag():
    # Cross-check only fires when symbol's margin ccy matches deposit ccy.
    # Use XAUUSD (margin ccy = USD = deposit ccy). Formula: 1 lot * 100 *
    # 4821 / 500 = 964.20 USD; broker says 970 USD → ~0.6% drift, no flag.
    sym = _xauusd_blob()
    result = size(
        request=SizingRequest(
            side="long",
            risk_pct=Decimal("1"),
            stop_points=100,
            broker_margin=Decimal("970"),
        ),
        account=AccountInfo.from_mcp(_usd_account_blob()),
        quote=Quote.from_mcp({"bid": "4820.50", "ask": "4821.00"}),
        sym=SymbolInfo.from_mcp(sym),
    )
    assert "MARGIN_CROSS_CHECK_DRIFT" not in result.flags
    assert result.margin_broker == Decimal("970")


def test_broker_margin_drift_flag():
    # Same XAUUSD setup but broker says 1500 — large drift.
    sym = _xauusd_blob()
    result = size(
        request=SizingRequest(
            side="long",
            risk_pct=Decimal("1"),
            stop_points=100,
            broker_margin=Decimal("1500"),
        ),
        account=AccountInfo.from_mcp(_usd_account_blob()),
        quote=Quote.from_mcp({"bid": "4820.50", "ask": "4821.00"}),
        sym=SymbolInfo.from_mcp(sym),
    )
    assert "MARGIN_CROSS_CHECK_DRIFT" in result.flags


def test_broker_margin_no_check_when_currencies_differ():
    # EURUSD with USD deposit: margin ccy is EUR. Cross-check is skipped
    # (note added) and no drift flag fires regardless of value.
    result = size(
        request=SizingRequest(
            side="long",
            risk_pct=Decimal("1"),
            stop_points=200,
            broker_margin=Decimal("999"),
        ),
        account=AccountInfo.from_mcp(_usd_account_blob()),
        quote=Quote.from_mcp(_quote_blob()),
        sym=SymbolInfo.from_mcp(_eurusd_blob()),
    )
    assert "MARGIN_CROSS_CHECK_DRIFT" not in result.flags
    assert any("cross-check skipped" in n for n in result.notes)


# --- Swap section ---------------------------------------------------------


def test_swap_section_for_long_eurusd_by_points():
    # swap_long = -2.5 in by_points mode, tick_value = 1 USD → -2.5 USD/lot/night.
    result = size(
        request=SizingRequest(
            side="long",
            risk_pct=Decimal("1"),
            stop_points=200,
            nights=3,
        ),
        account=AccountInfo.from_mcp(_usd_account_blob()),
        quote=Quote.from_mcp(_quote_blob()),
        sym=SymbolInfo.from_mcp(_eurusd_blob()),
    )
    assert result.daily_swap_long_per_lot == Decimal("-2.5")
    assert result.daily_swap_short_per_lot == Decimal("0.8")
    # 3 nights * 0.5 lot * -2.5 = -3.75 (ignores 3x rollover by design here)
    assert result.swap_for_holding == Decimal("-3.75")


def test_swap_holding_omitted_when_nights_is_zero():
    result = size(
        request=SizingRequest(
            side="long",
            risk_pct=Decimal("1"),
            stop_points=200,
        ),
        account=AccountInfo.from_mcp(_usd_account_blob()),
        quote=Quote.from_mcp(_quote_blob()),
        sym=SymbolInfo.from_mcp(_eurusd_blob()),
    )
    assert result.swap_for_holding is None


def test_swap_disabled_returns_zero_per_lot():
    sym = _eurusd_blob(swap_mode="disabled", swap_long="0", swap_short="0")
    result = size(
        request=SizingRequest(
            side="long",
            risk_pct=Decimal("1"),
            stop_points=200,
            nights=3,
        ),
        account=AccountInfo.from_mcp(_usd_account_blob()),
        quote=Quote.from_mcp(_quote_blob()),
        sym=SymbolInfo.from_mcp(sym),
    )
    assert result.daily_swap_long_per_lot == Decimal("0")
    assert result.swap_for_holding is None


# --- XAUUSD (CFDLeverage) end-to-end --------------------------------------


def test_xauusd_long_cfd_leverage():
    # XAUUSD: contract_size=100, price=4821, leverage=500 → margin = 964.20 USD.
    # tick_size = 0.01, tick_value = 1 USD (1 lot moves 100 USD per dollar).
    # 1% of 10_000 = 100 USD. Stop = 100 pts (one dollar) * 1 USD/tick = 100/lot.
    # Lot = 100/100 = 1 lot.
    sym_blob = {
        "name": "XAUUSD",
        "contract_size": "100",
        "tick_size": "0.01",
        "tick_value": "1",
        "volume_min": "0.01",
        "volume_max": "100",
        "volume_step": "0.01",
        "digits": 2,
        "calc_mode": "cfd_leverage",
        "swap_mode": "by_points",
        "swap_long": "-5",
        "swap_short": "1",
        "margin_initial": "0",
        "stops_level": 0,
        "currency_profit": "USD",
        "currency_margin": "USD",
    }
    result = size(
        request=SizingRequest(
            side="long",
            risk_pct=Decimal("1"),
            stop_points=100,
        ),
        account=AccountInfo.from_mcp(_usd_account_blob()),
        quote=Quote.from_mcp({"bid": "4820.50", "ask": "4821.00"}),
        sym=SymbolInfo.from_mcp(sym_blob),
    )
    assert result.lot_size == Decimal("1.00")
    assert result.cash_risk == Decimal("100")
    # Margin formula: 1 * 100 * 4821 / 500 = 964.20
    assert result.margin_formula == Decimal("964.20")


# --- UKOIL swing-trade scenario --------------------------------------------


def test_ukoil_long_swap_harvest_scenario():
    # User's example: long UKOIL pays $125/lot/night.
    # Configure swap_mode = by_deposit_currency, swap_long = 125.
    sym_blob = {
        "name": "UKOIL",
        "contract_size": "100",
        "tick_size": "0.01",
        "tick_value": "1",
        "volume_min": "0.01",
        "volume_max": "100",
        "volume_step": "0.01",
        "digits": 2,
        "calc_mode": "cfd_leverage",
        "swap_mode": "by_deposit_currency",
        "swap_long": "125",
        "swap_short": "-150",
        "margin_initial": "0",
        "stops_level": 0,
        "currency_profit": "USD",
        "currency_margin": "USD",
    }
    result = size(
        request=SizingRequest(
            side="long",
            risk_pct=Decimal("1"),
            stop_points=200,
            nights=10,
        ),
        account=AccountInfo.from_mcp(_usd_account_blob()),
        quote=Quote.from_mcp({"bid": "75.00", "ask": "75.10"}),
        sym=SymbolInfo.from_mcp(sym_blob),
    )
    assert result.daily_swap_long_per_lot == Decimal("125")
    # Cash risk per lot = 200 pts * 1 USD/pt = 200; lot = 100/200 = 0.5 lot.
    # Holding 10 nights at +125/lot/night * 0.5 lot = +625 USD.
    assert result.lot_size == Decimal("0.50")
    assert result.swap_for_holding == Decimal("625")
