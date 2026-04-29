"""Decimal helpers + safe parsing of mt5-mcp tool outputs.

mt5-mcp serialises Decimal money/price/volume fields as strings to avoid
float drift. This module is the single point where strings become Decimal
inside cfd_skills — any other module should accept Decimal already coerced.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Any


def D(value: Any) -> Decimal:
    """Coerce int / str / Decimal to Decimal via str() to avoid float drift.

    Floats are rejected — mt5-mcp's contract is "Decimal as string", and
    accepting floats here would let a binary-representation bug propagate
    silently into sizing math.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        raise TypeError(
            "decimal_io.D refuses floats — pass a Decimal or numeric string."
        )
    return Decimal(str(value))


def floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    """Floor `value` to the nearest multiple of `step`, away from oversize.

    Used for lot-size quantisation: if step=0.01 and raw lot=0.247, returns
    0.24 (floor, never round up — never let the user accidentally take more
    risk than requested).
    """
    if step <= 0:
        raise ValueError("step must be > 0")
    quantum = step
    quotient = (value / quantum).to_integral_value(rounding=ROUND_DOWN)
    return (quotient * quantum).quantize(quantum, rounding=ROUND_DOWN)


def quantize_price(value: Decimal, digits: int) -> Decimal:
    """Quantise a price to the symbol's digit count, half-up rounding."""
    if digits < 0:
        raise ValueError("digits must be >= 0")
    quantum = Decimal(1).scaleb(-digits)
    return value.quantize(quantum, rounding=ROUND_HALF_UP)
