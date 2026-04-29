"""Daily swap accrual per lot, in deposit currency.

Handles the swap modes commonly seen at retail brokers:

  - ``disabled`` — return zero
  - ``by_points`` — swap rate is in symbol points; convert via tick_value
  - ``by_deposit_currency`` — swap rate is already cash-per-lot in deposit ccy
  - ``by_base_currency`` / ``by_margin_currency`` — needs FX conversion
    rate (caller supplies it via ``fx_rate_to_deposit``)
  - ``by_interest_*`` — annualised %, treated as a 360-day approximation

For exotic modes we surface ``UnsupportedSwapMode`` rather than guess —
better to omit the swap section than to lie about a number that drives
holding decisions.

Friday-3x rollover: real per-day swap on the configured ``triple_swap_weekday``
is 3× the daily rate. The ``daily_swap`` function returns the *single-day*
rate; ``swap_for_nights(...)`` accumulates over a held period and applies
the 3× multiplier when the rollover day is crossed.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Literal, Optional

from cfd_skills.decimal_io import D


_WEEKDAY_INDEX: dict[str, int] = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


class UnsupportedSwapMode(ValueError):
    """Swap mode requires inputs we don't have or computation we don't do."""


def daily_swap_per_lot_in_deposit_ccy(
    *,
    side: Literal["long", "short"],
    swap_long: Decimal,
    swap_short: Decimal,
    swap_mode: str,
    contract_size: Decimal,
    digits: int,
    price: Decimal,
    tick_value: Decimal,
    fx_rate_to_deposit: Optional[Decimal] = None,
) -> Decimal:
    """Single-day swap accrual per 1 lot, in deposit currency.

    Sign convention: positive = credit, negative = debit. Returns the
    raw broker swap; multiply by ``volume`` and ``nights`` (with 3x on
    rollover) at the call site.
    """
    rate = D(swap_long) if side == "long" else D(swap_short)
    contract_size = D(contract_size)
    price = D(price)
    tick_value = D(tick_value)

    if swap_mode == "disabled" or rate == 0:
        return Decimal("0")

    if swap_mode == "by_deposit_currency":
        # Already in deposit ccy per lot per night.
        return rate

    if swap_mode == "by_points":
        # rate is in points; cash equivalent = rate * tick_value (per lot,
        # since tick_value is already deposit-ccy per tick per 1 lot).
        return rate * tick_value

    if swap_mode in ("by_base_currency", "by_margin_currency"):
        # Cash in margin/base currency = rate * contract_size (Forex
        # convention) — caller supplies the FX conversion to deposit.
        if fx_rate_to_deposit is None:
            raise UnsupportedSwapMode(
                f"{swap_mode} requires fx_rate_to_deposit (margin/base ccy → deposit ccy)."
            )
        cash_in_other_ccy = rate * contract_size
        return cash_in_other_ccy * D(fx_rate_to_deposit)

    if swap_mode in ("by_interest_current", "by_interest_open"):
        # Annualised %; per-day cash on the position notional. 360-day year
        # is the most common retail convention (some brokers use 365).
        per_lot_notional = price * contract_size
        return rate / Decimal("100") / Decimal("360") * per_lot_notional

    raise UnsupportedSwapMode(
        f"Swap mode '{swap_mode}' is not supported locally; "
        "either omit the swap output or extend swap_calc."
    )


def swap_for_nights(
    *,
    daily_swap: Decimal,
    volume: Decimal,
    nights: int,
    triple_swap_weekday: str,
    start_date: date,
) -> Decimal:
    """Total swap accrued holding ``volume`` lots for ``nights``, with 3x rollover.

    The rollover applies once per crossed instance of ``triple_swap_weekday``
    in the held window. ``start_date`` is the trade open date (broker timezone
    is fine; per-day granularity is enough for swap math).
    """
    if nights <= 0:
        return Decimal("0")
    daily_swap = D(daily_swap)
    volume = D(volume)
    rollover = _WEEKDAY_INDEX.get(triple_swap_weekday.lower())
    multiplier = Decimal("0")
    cur = start_date
    for _ in range(nights):
        cur = cur + timedelta(days=1)
        weekday = cur.weekday()  # Mon=0, Sun=6
        multiplier += Decimal("3") if weekday == rollover else Decimal("1")
    return daily_swap * volume * multiplier
