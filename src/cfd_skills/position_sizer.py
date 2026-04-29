"""Position-sizing orchestrator — pure function over pre-fetched MCP outputs.

The skill's bash entrypoint collects mt5-mcp tool results (account_info,
get_quote, get_symbols, optionally calc_margin), bundles them into a
``SizingRequest``, and calls ``size(...)`` here. This module does not
make any I/O — it's deterministic, easy to test, and easy for the LLM
to reason about.

Cash-risk math is straightforward thanks to mt5-mcp ``tick_value``
(already in deposit currency, per-tick, per-1-lot):

    cash_risk_per_lot = stop_distance_in_ticks × tick_value
    lot_size          = floor_to_step(risk_amount / cash_risk_per_lot)

Margin uses ``cfd_skills.margin_calc`` for transparency, then cross-checks
against the broker-authoritative ``calc_margin`` result if supplied.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal, Optional

from cfd_skills.decimal_io import D, floor_to_step
from cfd_skills.margin_calc import (
    MissingMarginInput,
    UnsupportedCalcMode,
    apply_margin_rate,
    base_margin,
)
from cfd_skills.swap_calc import (
    UnsupportedSwapMode,
    daily_swap_per_lot_in_deposit_ccy,
)


# --- Inputs ----------------------------------------------------------------


@dataclass(frozen=True)
class AccountInfo:
    equity: Decimal
    free_margin: Decimal
    leverage: int
    currency: str        # deposit currency

    @classmethod
    def from_mcp(cls, blob: dict) -> "AccountInfo":
        return cls(
            equity=D(blob["equity"]),
            free_margin=D(blob["margin_free"]),
            leverage=int(blob["leverage"]),
            currency=blob["currency"],
        )


@dataclass(frozen=True)
class Quote:
    bid: Decimal
    ask: Decimal

    @classmethod
    def from_mcp(cls, blob: dict) -> "Quote":
        return cls(bid=D(blob["bid"]), ask=D(blob["ask"]))


@dataclass(frozen=True)
class SymbolInfo:
    name: str
    contract_size: Decimal
    tick_size: Decimal
    tick_value: Decimal
    volume_min: Decimal
    volume_max: Decimal
    volume_step: Decimal
    digits: int
    calc_mode: str
    swap_mode: str
    swap_long: Decimal
    swap_short: Decimal
    margin_initial: Decimal
    stops_level: int
    currency_profit: str
    currency_margin: str

    @classmethod
    def from_mcp(cls, blob: dict) -> "SymbolInfo":
        return cls(
            name=blob["name"],
            contract_size=D(blob["contract_size"]),
            tick_size=D(blob["tick_size"]),
            tick_value=D(blob["tick_value"]),
            volume_min=D(blob["volume_min"]),
            volume_max=D(blob["volume_max"]),
            volume_step=D(blob["volume_step"]),
            digits=int(blob["digits"]),
            calc_mode=blob["calc_mode"],
            swap_mode=blob["swap_mode"],
            swap_long=D(blob["swap_long"]),
            swap_short=D(blob["swap_short"]),
            margin_initial=D(blob["margin_initial"]),
            stops_level=int(blob["stops_level"]),
            currency_profit=blob["currency_profit"],
            currency_margin=blob["currency_margin"],
        )


@dataclass(frozen=True)
class SizingRequest:
    side: Literal["long", "short"]
    # Exactly one of these two.
    risk_pct: Optional[Decimal] = None     # e.g. Decimal("1.0") for 1%
    risk_amount: Optional[Decimal] = None  # absolute deposit-ccy amount
    # Stop distance, expressed one of two ways.
    stop_price: Optional[Decimal] = None       # absolute stop level
    stop_points: Optional[int] = None          # distance in symbol points
    # Optional: planned holding nights for the swap-aware output.
    nights: int = 0
    # Optional: broker-authoritative margin from `calc_margin` MCP tool.
    broker_margin: Optional[Decimal] = None
    # Sanity-flag thresholds (overridable from config).
    margin_warning_pct: Decimal = field(default_factory=lambda: D("30"))


# --- Outputs ---------------------------------------------------------------


@dataclass
class SizingResult:
    symbol: str
    side: str
    lot_size: Decimal                      # rounded to volume_step
    notional: Decimal                      # volume × contract_size × price (in margin ccy)
    stop_price: Decimal
    stop_distance_points: int
    cash_risk: Decimal                     # in deposit ccy at proposed stop
    risk_pct_of_equity: Decimal
    margin_formula: Decimal                # base_margin (margin ccy)
    margin_broker: Optional[Decimal]       # from calc_margin if supplied (deposit ccy)
    margin_pct_of_free: Optional[Decimal]  # using whichever margin we trust
    daily_swap_long_per_lot: Decimal       # deposit ccy
    daily_swap_short_per_lot: Decimal      # deposit ccy
    swap_for_holding: Optional[Decimal]    # over `nights`, applied to chosen side+volume
    flags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# --- Internal helpers ------------------------------------------------------


def _resolve_risk_amount(req: SizingRequest, account: AccountInfo) -> Decimal:
    if req.risk_amount is not None:
        return D(req.risk_amount)
    if req.risk_pct is not None:
        return account.equity * D(req.risk_pct) / D("100")
    raise ValueError("Provide either risk_pct or risk_amount.")


def _resolve_stop(
    req: SizingRequest, quote: Quote, sym: SymbolInfo
) -> tuple[Decimal, int]:
    """Return (stop_price, stop_distance_points)."""
    entry = quote.ask if req.side == "long" else quote.bid
    if req.stop_price is not None:
        stop_price = D(req.stop_price)
        distance = abs(entry - stop_price)
    elif req.stop_points is not None:
        points = int(req.stop_points)
        if req.side == "long":
            stop_price = entry - sym.tick_size * points
        else:
            stop_price = entry + sym.tick_size * points
        distance = sym.tick_size * points
    else:
        raise ValueError("Provide either stop_price or stop_points.")
    distance_points = int((distance / sym.tick_size).to_integral_value())
    return stop_price, distance_points


# --- Main entry ------------------------------------------------------------


def size(
    *,
    request: SizingRequest,
    account: AccountInfo,
    quote: Quote,
    sym: SymbolInfo,
) -> SizingResult:
    """Compute lot size + margin + swap output, append sanity flags."""
    risk_amount = _resolve_risk_amount(request, account)
    stop_price, stop_points = _resolve_stop(request, quote, sym)
    entry = quote.ask if request.side == "long" else quote.bid
    flags: list[str] = []
    notes: list[str] = []

    # ---- Cash-risk-per-lot math ----------------------------------------
    cash_risk_per_lot = D(stop_points) * sym.tick_value
    if cash_risk_per_lot <= 0:
        flags.append("ZERO_RISK_PER_LOT")
        cash_risk_per_lot = Decimal("0")

    # ---- Lot size ------------------------------------------------------
    if cash_risk_per_lot == 0:
        raw_lot = Decimal("0")
    else:
        raw_lot = risk_amount / cash_risk_per_lot
    lot = floor_to_step(raw_lot, sym.volume_step)

    if lot < sym.volume_min:
        flags.append("BELOW_MIN_LOT")
        notes.append(
            f"Computed lot {lot} is below broker minimum {sym.volume_min}; "
            "either widen the stop, raise risk, or skip this trade."
        )
        lot = sym.volume_min  # report the constraint, but don't auto-execute
    if lot > sym.volume_max:
        flags.append("ABOVE_MAX_LOT")
        lot = sym.volume_max

    # ---- Cash risk at the proposed lot ---------------------------------
    cash_risk = lot * cash_risk_per_lot
    risk_pct_of_equity = (
        cash_risk / account.equity * D("100") if account.equity > 0 else Decimal("0")
    )

    # ---- Notional ------------------------------------------------------
    notional = lot * sym.contract_size * entry

    # ---- Margin --------------------------------------------------------
    margin_formula: Decimal
    try:
        raw = base_margin(
            calc_mode=sym.calc_mode,
            volume=lot,
            contract_size=sym.contract_size,
            price=entry,
            leverage=account.leverage,
            margin_initial=sym.margin_initial,
            tick_value=sym.tick_value,
            tick_size=sym.tick_size,
        )
        margin_formula = apply_margin_rate(raw, D("1"))
    except (UnsupportedCalcMode, MissingMarginInput) as exc:
        margin_formula = Decimal("0")
        flags.append("FORMULA_MARGIN_UNAVAILABLE")
        notes.append(f"Local margin formula skipped: {exc}")

    margin_broker = D(request.broker_margin) if request.broker_margin is not None else None

    # Cross-check formula vs. broker only when both are in the same currency
    # (margin formula returns values in the symbol's margin currency; the
    # broker's calc_margin returns deposit-currency). For FX where margin
    # currency = base currency != deposit, we skip the comparison to avoid
    # false drift flags. Margin/deposit equality covers the common metals,
    # crypto, and index CFD cases.
    currencies_match = sym.currency_margin == account.currency
    if margin_broker is not None and margin_formula > 0 and currencies_match:
        diff_pct = abs(margin_broker - margin_formula) / margin_broker * D("100")
        if diff_pct > D("2"):
            flags.append("MARGIN_CROSS_CHECK_DRIFT")
            notes.append(
                f"Formula margin {margin_formula} differs from broker {margin_broker} "
                f"by {diff_pct:.2f}% — broker value is authoritative."
            )
    elif margin_broker is not None and not currencies_match:
        notes.append(
            f"Margin cross-check skipped: formula in {sym.currency_margin}, "
            f"broker in {account.currency}. Broker value is authoritative."
        )

    # margin_pct_of_free needs deposit-currency margin. Prefer broker value
    # when present (always deposit-ccy); fall back to formula only if margin
    # currency matches deposit.
    margin_for_check: Optional[Decimal] = None
    if margin_broker is not None:
        margin_for_check = margin_broker
    elif currencies_match and margin_formula > 0:
        margin_for_check = margin_formula
    margin_pct_of_free = (
        margin_for_check / account.free_margin * D("100")
        if margin_for_check is not None and account.free_margin > 0
        else None
    )
    if margin_pct_of_free is not None and margin_pct_of_free > request.margin_warning_pct:
        flags.append("HIGH_MARGIN_USAGE")
        notes.append(
            f"Margin would consume {margin_pct_of_free:.1f}% of free margin "
            f"(threshold {request.margin_warning_pct}%)."
        )

    # ---- Stop-distance sanity flags ------------------------------------
    spread_points = int(((quote.ask - quote.bid) / sym.tick_size).to_integral_value())
    if stop_points < sym.stops_level:
        flags.append("STOP_INSIDE_BROKER_MIN")
        notes.append(
            f"Stop {stop_points} pts is closer than broker minimum "
            f"{sym.stops_level} pts; broker will reject."
        )
    if spread_points > 0 and stop_points < spread_points * 2:
        flags.append("STOP_TIGHTER_THAN_2X_SPREAD")
        notes.append(
            f"Stop {stop_points} pts is < 2x current spread ({spread_points} pts); "
            "high whipsaw risk."
        )

    # ---- Swap section --------------------------------------------------
    daily_long = Decimal("0")
    daily_short = Decimal("0")
    try:
        daily_long = daily_swap_per_lot_in_deposit_ccy(
            side="long", swap_long=sym.swap_long, swap_short=sym.swap_short,
            swap_mode=sym.swap_mode, contract_size=sym.contract_size,
            digits=sym.digits, price=entry, tick_value=sym.tick_value,
        )
        daily_short = daily_swap_per_lot_in_deposit_ccy(
            side="short", swap_long=sym.swap_long, swap_short=sym.swap_short,
            swap_mode=sym.swap_mode, contract_size=sym.contract_size,
            digits=sym.digits, price=entry, tick_value=sym.tick_value,
        )
    except UnsupportedSwapMode as exc:
        flags.append("SWAP_MODE_UNSUPPORTED")
        notes.append(f"Swap output omitted: {exc}")

    swap_for_holding: Optional[Decimal] = None
    if request.nights > 0 and (daily_long != 0 or daily_short != 0):
        side_swap = daily_long if request.side == "long" else daily_short
        # Per-night × nights, ignoring 3x rollover here — caller can use
        # swap_for_nights() with a real start_date for precision. The
        # SizingRequest deliberately doesn't carry a date because a sizer
        # call typically doesn't know the open date yet.
        swap_for_holding = side_swap * lot * D(request.nights)

    return SizingResult(
        symbol=sym.name,
        side=request.side,
        lot_size=lot,
        notional=notional,
        stop_price=stop_price,
        stop_distance_points=stop_points,
        cash_risk=cash_risk,
        risk_pct_of_equity=risk_pct_of_equity,
        margin_formula=margin_formula,
        margin_broker=margin_broker,
        margin_pct_of_free=margin_pct_of_free,
        daily_swap_long_per_lot=daily_long,
        daily_swap_short_per_lot=daily_short,
        swap_for_holding=swap_for_holding,
        flags=flags,
        notes=notes,
    )
