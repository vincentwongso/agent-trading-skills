"""Per-EnCalcMode margin formulas, ported from cfd-claculator (Decimal-typed).

Reference: https://github.com/.../cfd-claculator/docs/mt5/margin_requirements_formula.md

Two-step computation:

  1. ``base_margin(...)`` returns the raw margin in the symbol's *margin
     currency*. The caller converts to deposit currency if they differ
     (mt5-mcp ``tick_value`` is already deposit-ccy, but ``contract_size *
     price`` is in margin-ccy and needs FX conversion).
  2. ``apply_margin_rate(...)`` multiplies by the applicable buy/sell
     margin rate from ``MarginInitialBuy`` / ``MarginInitialSell``.

For broker cross-checks, prefer the ``calc_margin`` MCP tool over this
module — the broker's own answer accounts for hedging discounts and
broker-specific calc-mode tweaks. This module exists for transparency
("here's WHY the margin is X") and for the case where the broker call
fails (market closed, etc.).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from trading_agent_skills.decimal_io import D


# String enum values matching mt5-mcp's SymbolInfo.calc_mode.
CALC_FOREX = "forex"
CALC_FOREX_NO_LEVERAGE = "forex_no_leverage"
CALC_FUTURES = "futures"
CALC_CFD = "cfd"
CALC_CFD_INDEX = "cfd_index"
CALC_CFD_LEVERAGE = "cfd_leverage"
CALC_EXCH_STOCKS = "exch_stocks"
CALC_EXCH_FUTURES = "exch_futures"
CALC_EXCH_FUTURES_FORTS = "exch_futures_forts"
CALC_EXCH_OPTIONS = "exch_options"
CALC_EXCH_OPTIONS_MARGIN = "exch_options_margin"
CALC_EXCH_BONDS = "exch_bonds"
CALC_EXCH_STOCKS_MOEX = "exch_stocks_moex"
CALC_EXCH_BONDS_MOEX = "exch_bonds_moex"
CALC_SERV_COLLATERAL = "serv_collateral"


class UnsupportedCalcMode(ValueError):
    """The symbol's calc mode isn't implemented locally; use ``calc_margin`` MCP tool."""


class MissingMarginInput(ValueError):
    """A required input for this calc mode wasn't supplied (e.g. tick_value for cfd_index)."""


def base_margin(
    *,
    calc_mode: str,
    volume: Decimal,
    contract_size: Decimal,
    price: Decimal,
    leverage: int,
    margin_initial: Decimal = Decimal("0"),
    tick_value: Optional[Decimal] = None,
    tick_size: Optional[Decimal] = None,
    face_value: Optional[Decimal] = None,
) -> Decimal:
    """Raw margin in the symbol's margin currency (caller converts to deposit ccy).

    Mirrors ``calculateBaseMargin`` from cfd-claculator/src/lib/calculations/margin.ts.
    """
    volume = D(volume)
    contract_size = D(contract_size)
    price = D(price)
    margin_initial = D(margin_initial)
    leverage_d = D(leverage)

    if calc_mode == CALC_FOREX:
        if margin_initial > 0:
            return volume * margin_initial / leverage_d
        return volume * contract_size / leverage_d

    if calc_mode == CALC_FOREX_NO_LEVERAGE:
        return volume * contract_size

    if calc_mode in (CALC_CFD, CALC_EXCH_STOCKS, CALC_EXCH_STOCKS_MOEX):
        return volume * contract_size * price

    if calc_mode == CALC_CFD_LEVERAGE:
        if margin_initial > 0:
            return volume * margin_initial / leverage_d
        return volume * contract_size * price / leverage_d

    if calc_mode == CALC_CFD_INDEX:
        if tick_value is None or tick_size is None:
            raise MissingMarginInput("cfd_index requires tick_value and tick_size")
        return volume * contract_size * price * D(tick_value) / D(tick_size)

    if calc_mode in (CALC_FUTURES, CALC_EXCH_FUTURES, CALC_EXCH_OPTIONS_MARGIN):
        if margin_initial > 0:
            return volume * margin_initial
        raise MissingMarginInput(
            f"{calc_mode} requires margin_initial > 0 (broker config)."
        )

    if calc_mode == CALC_EXCH_OPTIONS:
        if margin_initial > 0:
            return volume * margin_initial
        return volume * contract_size * price

    if calc_mode in (CALC_EXCH_BONDS, CALC_EXCH_BONDS_MOEX):
        if face_value is None:
            raise MissingMarginInput("exch_bonds requires face_value")
        return volume * contract_size * D(face_value) * price / D(100)

    if calc_mode == CALC_SERV_COLLATERAL:
        return Decimal("0")

    if calc_mode == CALC_EXCH_FUTURES_FORTS:
        # Moscow Exchange derivatives — bilateral margin not supported locally.
        raise UnsupportedCalcMode(
            "exch_futures_forts margin not supported; use calc_margin MCP tool."
        )

    raise UnsupportedCalcMode(f"Unknown calc_mode: {calc_mode}")


def apply_margin_rate(margin: Decimal, rate: Decimal) -> Decimal:
    """Apply broker's per-side margin rate (``MarginInitialBuy``/``Sell``).

    A rate of 1.0 means "no adjustment". Lower rates (e.g. 0.5) reduce
    margin (broker discount); higher rates penalise.
    """
    return D(margin) * D(rate)
