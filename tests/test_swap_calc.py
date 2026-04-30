"""swap_calc: per-night swap accrual + multi-night accumulation with 3x rollover."""

from datetime import date
from decimal import Decimal

import pytest

from trading_agent_skills.swap_calc import (
    UnsupportedSwapMode,
    daily_swap_per_lot_in_deposit_ccy,
    swap_for_nights,
)


# --- daily_swap_per_lot_in_deposit_ccy --------------------------------------


def test_disabled_returns_zero():
    assert daily_swap_per_lot_in_deposit_ccy(
        side="long",
        swap_long=Decimal("-2.5"), swap_short=Decimal("0.8"),
        swap_mode="disabled",
        contract_size=Decimal("100000"), digits=5,
        price=Decimal("1.0824"), tick_value=Decimal("1"),
    ) == Decimal("0")


def test_by_deposit_currency_passes_through():
    # UKOIL-style: broker says "long earns +$125/lot/night" directly.
    cash = daily_swap_per_lot_in_deposit_ccy(
        side="long",
        swap_long=Decimal("125"), swap_short=Decimal("-150"),
        swap_mode="by_deposit_currency",
        contract_size=Decimal("100"), digits=2,
        price=Decimal("75"), tick_value=Decimal("1"),
    )
    assert cash == Decimal("125")


def test_by_deposit_currency_short_side():
    cash = daily_swap_per_lot_in_deposit_ccy(
        side="short",
        swap_long=Decimal("125"), swap_short=Decimal("-150"),
        swap_mode="by_deposit_currency",
        contract_size=Decimal("100"), digits=2,
        price=Decimal("75"), tick_value=Decimal("1"),
    )
    assert cash == Decimal("-150")


def test_by_points_uses_tick_value():
    # Rate of 1 point with tick_value of 10 USD/tick = 10 USD/night per lot.
    cash = daily_swap_per_lot_in_deposit_ccy(
        side="long",
        swap_long=Decimal("1"), swap_short=Decimal("-1"),
        swap_mode="by_points",
        contract_size=Decimal("100000"), digits=5,
        price=Decimal("1.0824"), tick_value=Decimal("10"),
    )
    assert cash == Decimal("10")


def test_zero_rate_short_circuits_even_if_unsupported_mode():
    # When the rate is 0, return 0 without raising — saves callers from
    # special-casing "this symbol has no swap".
    assert daily_swap_per_lot_in_deposit_ccy(
        side="long",
        swap_long=Decimal("0"), swap_short=Decimal("0"),
        swap_mode="by_reopen_bid",  # unsupported, but rate is 0
        contract_size=Decimal("100"), digits=2,
        price=Decimal("75"), tick_value=Decimal("1"),
    ) == Decimal("0")


def test_by_base_currency_requires_fx_rate():
    with pytest.raises(UnsupportedSwapMode):
        daily_swap_per_lot_in_deposit_ccy(
            side="long",
            swap_long=Decimal("-2.5"), swap_short=Decimal("0.8"),
            swap_mode="by_base_currency",
            contract_size=Decimal("100000"), digits=5,
            price=Decimal("1.0824"), tick_value=Decimal("1"),
        )


def test_by_base_currency_with_fx_rate_converts():
    # Rate -2.5 EUR/lot with EURUSD = 1.10 → -2.75 USD/lot... no wait,
    # rate * contract_size = -2.5 * 100_000 = -250_000 EUR??? That can't
    # be right — by_base_currency rates are typically tiny per-unit.
    # Actually mt5lib's "currency_symbol" mode means the rate is already
    # cash-per-lot in base ccy. Test reflects that.
    cash = daily_swap_per_lot_in_deposit_ccy(
        side="long",
        swap_long=Decimal("-2.5"), swap_short=Decimal("0.8"),
        swap_mode="by_base_currency",
        contract_size=Decimal("1"),  # using contract_size=1 to test pure rate path
        digits=5,
        price=Decimal("1.0824"), tick_value=Decimal("1"),
        fx_rate_to_deposit=Decimal("1.10"),
    )
    # -2.5 * 1 * 1.10 = -2.75
    assert cash == Decimal("-2.750")


def test_by_interest_uses_360_day_year():
    # 5% annualised on a 100_000 EUR notional = 100_000 * 0.05 / 360 ≈ 13.89/day
    cash = daily_swap_per_lot_in_deposit_ccy(
        side="long",
        swap_long=Decimal("5"), swap_short=Decimal("-3"),
        swap_mode="by_interest_current",
        contract_size=Decimal("100000"), digits=5,
        price=Decimal("1"), tick_value=Decimal("1"),
    )
    # 1 * 100_000 * 0.05 / 360
    assert cash > Decimal("13.88") and cash < Decimal("13.89")


def test_unsupported_mode_with_nonzero_rate_raises():
    with pytest.raises(UnsupportedSwapMode):
        daily_swap_per_lot_in_deposit_ccy(
            side="long",
            swap_long=Decimal("1.5"), swap_short=Decimal("-1"),
            swap_mode="by_reopen_bid",
            contract_size=Decimal("100000"), digits=5,
            price=Decimal("1.0824"), tick_value=Decimal("1"),
        )


# --- swap_for_nights -------------------------------------------------------


def test_zero_nights_returns_zero():
    assert swap_for_nights(
        daily_swap=Decimal("125"), volume=Decimal("1"),
        nights=0, triple_swap_weekday="wednesday",
        start_date=date(2026, 4, 27),
    ) == Decimal("0")


def test_one_night_no_rollover():
    # Mon → Tue, no Wed crossed.
    assert swap_for_nights(
        daily_swap=Decimal("10"), volume=Decimal("1"),
        nights=1, triple_swap_weekday="wednesday",
        start_date=date(2026, 4, 27),  # Monday
    ) == Decimal("10")


def test_multi_night_includes_3x_on_rollover_day():
    # Open Mon 2026-04-27, hold 3 nights → Tue + Wed + Thu nights.
    # Wed gets 3x; Tue/Thu are 1x. Total = 1 + 3 + 1 = 5 day-equivalents.
    total = swap_for_nights(
        daily_swap=Decimal("10"), volume=Decimal("1"),
        nights=3, triple_swap_weekday="wednesday",
        start_date=date(2026, 4, 27),  # Mon
    )
    assert total == Decimal("50")


def test_volume_scales_swap():
    total = swap_for_nights(
        daily_swap=Decimal("10"), volume=Decimal("2.5"),
        nights=1, triple_swap_weekday="wednesday",
        start_date=date(2026, 4, 27),
    )
    assert total == Decimal("25")


def test_friday_3x_rollover_for_indices():
    # Some equity-index brokers triple on Fridays instead of Wednesdays.
    # Open Thu 2026-04-30, hold 1 night → Fri (rollover) → 3x.
    total = swap_for_nights(
        daily_swap=Decimal("10"), volume=Decimal("1"),
        nights=1, triple_swap_weekday="friday",
        start_date=date(2026, 4, 30),  # Thursday
    )
    assert total == Decimal("30")
