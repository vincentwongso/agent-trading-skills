"""margin_calc: EnCalcMode dispatch ported from cfd-claculator margin.test.ts.

Numbers are 1:1 with the TypeScript fixtures so the two implementations stay
in sync. Any drift here vs. cfd-claculator is a bug in one of them.
"""

from decimal import Decimal

import pytest

from cfd_skills.margin_calc import (
    MissingMarginInput,
    UnsupportedCalcMode,
    apply_margin_rate,
    base_margin,
)


# --- Forex (CalcMode 0) ----------------------------------------------------


def test_forex_usdjpy_volume_x_contract_size_div_leverage():
    base = base_margin(
        calc_mode="forex",
        volume=Decimal("1"),
        contract_size=Decimal("100000"),
        price=Decimal("150"),
        leverage=500,
    )
    assert base == Decimal("200")  # in USD (margin = base = USD for USDJPY)


def test_forex_eurusd_in_margin_currency_before_conversion():
    # 1 lot of EURUSD = 200 EUR (margin currency); caller converts to deposit.
    base = base_margin(
        calc_mode="forex",
        volume=Decimal("1"),
        contract_size=Decimal("100000"),
        price=Decimal("1.13"),
        leverage=500,
    )
    assert base == Decimal("200")


def test_forex_uses_margin_initial_scalar_when_positive():
    base = base_margin(
        calc_mode="forex",
        volume=Decimal("1"),
        contract_size=Decimal("100000"),
        price=Decimal("1"),
        leverage=500,
        margin_initial=Decimal("50000"),
    )
    assert base == Decimal("100")  # 50_000 / 500


# --- CFD (CalcMode 2) ------------------------------------------------------


def test_cfd_btcusd_volume_x_contract_x_price_no_leverage():
    base = base_margin(
        calc_mode="cfd",
        volume=Decimal("1"),
        contract_size=Decimal("1"),
        price=Decimal("74360"),
        leverage=500,
    )
    assert base == Decimal("74360")


def test_cfd_adausd_scales_with_contract_size():
    base = base_margin(
        calc_mode="cfd",
        volume=Decimal("1"),
        contract_size=Decimal("100"),
        price=Decimal("0.24"),
        leverage=500,
    )
    assert base == Decimal("24.00")


# --- CFDLeverage (CalcMode 4) ---------------------------------------------


def test_cfd_leverage_xauusd():
    base = base_margin(
        calc_mode="cfd_leverage",
        volume=Decimal("1"),
        contract_size=Decimal("100"),
        price=Decimal("4821"),
        leverage=500,
    )
    assert base == Decimal("964.2")


def test_cfd_leverage_xagusd_scales_with_contract_size():
    base = base_margin(
        calc_mode="cfd_leverage",
        volume=Decimal("1"),
        contract_size=Decimal("5000"),
        price=Decimal("30"),
        leverage=500,
    )
    assert base == Decimal("300")


def test_cfd_leverage_uses_margin_initial_when_positive():
    base = base_margin(
        calc_mode="cfd_leverage",
        volume=Decimal("1"),
        contract_size=Decimal("100"),
        price=Decimal("4821"),
        leverage=500,
        margin_initial=Decimal("100000"),
    )
    assert base == Decimal("200")


# --- ForexNoLeverage (CalcMode 5) ------------------------------------------


def test_forex_no_leverage_full_notional():
    base = base_margin(
        calc_mode="forex_no_leverage",
        volume=Decimal("1"),
        contract_size=Decimal("100000"),
        price=Decimal("1"),
        leverage=500,  # ignored
    )
    assert base == Decimal("100000")


# --- CFDIndex (CalcMode 3) -------------------------------------------------


def test_cfd_index_includes_tick_ratio():
    base = base_margin(
        calc_mode="cfd_index",
        volume=Decimal("1"),
        contract_size=Decimal("1"),
        price=Decimal("5000"),
        leverage=500,
        tick_value=Decimal("1"),
        tick_size=Decimal("0.1"),
    )
    assert base == Decimal("50000")


def test_cfd_index_requires_tick_value_and_size():
    with pytest.raises(MissingMarginInput):
        base_margin(
            calc_mode="cfd_index",
            volume=Decimal("1"),
            contract_size=Decimal("1"),
            price=Decimal("1"),
            leverage=500,
        )


# --- Futures (CalcMode 1) --------------------------------------------------


def test_futures_uses_margin_initial_per_lot():
    base = base_margin(
        calc_mode="futures",
        volume=Decimal("2"),
        contract_size=Decimal("1"),
        price=Decimal("100"),
        leverage=500,
        margin_initial=Decimal("1500"),
    )
    assert base == Decimal("3000")


def test_futures_without_margin_initial_raises():
    with pytest.raises(MissingMarginInput):
        base_margin(
            calc_mode="futures",
            volume=Decimal("1"),
            contract_size=Decimal("1"),
            price=Decimal("100"),
            leverage=500,
        )


# --- ServCollateral (CalcMode 64) ------------------------------------------


def test_serv_collateral_returns_zero():
    base = base_margin(
        calc_mode="serv_collateral",
        volume=Decimal("1"),
        contract_size=Decimal("1"),
        price=Decimal("1"),
        leverage=500,
    )
    assert base == Decimal("0")


# --- ExchForts -- not supported locally ------------------------------------


def test_exch_futures_forts_raises_unsupported():
    with pytest.raises(UnsupportedCalcMode):
        base_margin(
            calc_mode="exch_futures_forts",
            volume=Decimal("1"),
            contract_size=Decimal("1"),
            price=Decimal("1"),
            leverage=500,
        )


def test_unknown_calc_mode_raises_unsupported():
    with pytest.raises(UnsupportedCalcMode):
        base_margin(
            calc_mode="bogus",
            volume=Decimal("1"),
            contract_size=Decimal("1"),
            price=Decimal("1"),
            leverage=500,
        )


# --- apply_margin_rate -----------------------------------------------------


def test_apply_margin_rate_btcusd_full_flow():
    base = base_margin(
        calc_mode="cfd",
        volume=Decimal("1"),
        contract_size=Decimal("1"),
        price=Decimal("74360"),
        leverage=500,
    )
    final = apply_margin_rate(base, Decimal("0.02"))
    assert final == Decimal("1487.20")


def test_apply_margin_rate_identity_when_one():
    base = base_margin(
        calc_mode="forex",
        volume=Decimal("1"),
        contract_size=Decimal("100000"),
        price=Decimal("150"),
        leverage=500,
    )
    assert apply_margin_rate(base, Decimal("1")) == Decimal("200")
