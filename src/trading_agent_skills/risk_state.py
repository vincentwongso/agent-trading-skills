"""Position classification + drawdown-to-SL math for ``daily-risk-guardian``.

The classification itself (AT_RISK / RISK_FREE / LOCKED_PROFIT) is **LLM-
judged** by the agent — see the plan's "Risk-free predicate (LLM-judged)"
section. The agent inspects each open position's SL location, news,
fundamentals, and price action, then writes a classification + short
reasoning into the JSON bundle. This module is the type-safe receiver:

- ``Position.from_mcp`` accepts the agent's per-position blob and defaults to
  ``AT_RISK`` if the agent didn't classify (false-positive RISK_FREE is the
  dangerous error — never default to it).
- ``drawdown_to_sl`` is the deposit-currency loss the position would take
  if the stop hits. Positive = loss, negative = locked profit.
- ``cash_at_risk`` is ``max(0, drawdown_to_sl)`` so AT_RISK positions
  contribute their downside to the daily-cap math while LOCKED_PROFIT
  positions don't add (or subtract) from the loss budget.

This module is pure — no I/O, no broker calls. Tests pass plain dicts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal, Optional

from trading_agent_skills.decimal_io import D


class Classification(str, Enum):
    AT_RISK = "AT_RISK"
    RISK_FREE = "RISK_FREE"
    LOCKED_PROFIT = "LOCKED_PROFIT"


@dataclass(frozen=True)
class Position:
    ticket: int
    symbol: str
    side: Literal["long", "short"]
    volume: Decimal
    entry_price: Decimal
    sl: Optional[Decimal]
    tp: Optional[Decimal]
    current_price: Decimal
    unrealized_pnl: Decimal       # deposit ccy, broker-reported
    swap_accrued: Decimal          # deposit ccy, broker-reported
    open_time_utc: datetime
    tick_size: Decimal
    tick_value: Decimal            # deposit ccy per tick per 1 lot
    classification: Classification = Classification.AT_RISK
    classification_reason: str = ""

    @classmethod
    def from_mcp(
        cls,
        *,
        position: dict[str, Any],
        symbol: dict[str, Any],
        classification: Optional[str] = None,
        classification_reason: str = "",
    ) -> "Position":
        """Build a Position from raw mt5-mcp shapes plus an optional override."""
        side_raw = position.get("side") or position.get("type")
        if side_raw is None:
            raise KeyError("position bundle missing 'side' (or 'type')")
        side = side_raw.lower()
        if side not in ("long", "short"):
            raise ValueError(f"unsupported side: {side_raw!r}")

        sl_raw = position.get("sl")
        tp_raw = position.get("tp")
        open_time = position.get("open_time") or position.get("time_setup")
        if open_time is None:
            raise KeyError("position bundle missing 'open_time'")
        if isinstance(open_time, str):
            open_time_dt = datetime.fromisoformat(open_time)
        elif isinstance(open_time, datetime):
            open_time_dt = open_time
        else:
            raise TypeError("open_time must be ISO string or datetime")

        cls_value = (
            Classification(classification.upper())
            if classification is not None
            else Classification.AT_RISK
        )

        return cls(
            ticket=int(position["ticket"]),
            symbol=str(position["symbol"]),
            side=side,  # type: ignore[arg-type]
            volume=D(position["volume"]),
            entry_price=D(position.get("price_open") or position["entry_price"]),
            sl=D(sl_raw) if sl_raw not in (None, "", 0, "0") else None,
            tp=D(tp_raw) if tp_raw not in (None, "", 0, "0") else None,
            current_price=D(
                position.get("price_current") or position["current_price"]
            ),
            unrealized_pnl=D(
                position.get("profit", position.get("unrealized_pnl", "0"))
            ),
            swap_accrued=D(position.get("swap", position.get("swap_accrued", "0"))),
            open_time_utc=open_time_dt,
            tick_size=D(symbol["tick_size"]),
            tick_value=D(symbol["tick_value"]),
            classification=cls_value,
            classification_reason=classification_reason,
        )


# ---------- Math -----------------------------------------------------------


def _side_factor(side: str) -> int:
    return 1 if side == "long" else -1


def drawdown_to_sl(p: Position) -> Optional[Decimal]:
    """Return the deposit-ccy P&L delta from current price to the stop.

    Sign convention:
      - Positive Decimal = loss the position would take if SL hits
      - Negative Decimal = profit that's already locked in by the stop
      - ``None`` if no SL is set on the position

    Implementation: ``(sl - entry) × side_factor × tick_value / tick_size × volume``
    gives signed P&L at SL. Multiply by ``-1`` to express it as a "drawdown"
    (positive when adverse).
    """
    if p.sl is None:
        return None
    if p.tick_size <= 0 or p.tick_value <= 0:
        return None
    sf = _side_factor(p.side)
    pnl_at_sl = (p.sl - p.entry_price) * sf * p.tick_value / p.tick_size * p.volume
    return -pnl_at_sl


def cash_at_risk(p: Position) -> Decimal:
    """Floor of drawdown_to_sl at zero — the worst-case **loss** to the cap.

    LOCKED_PROFIT and breakeven positions return 0 (they don't add to the loss
    budget). Positions without SL are treated as full unrealized — the caller
    surfaces a flag.
    """
    dd = drawdown_to_sl(p)
    if dd is None:
        return Decimal("0")
    return dd if dd > 0 else Decimal("0")


def at_risk_loss(p: Position) -> Decimal:
    """Cash loss this position contributes IF classification is AT_RISK,
    else zero. RISK_FREE / LOCKED_PROFIT positions don't consume cap.
    """
    if p.classification != Classification.AT_RISK:
        return Decimal("0")
    return cash_at_risk(p)


def position_risk_pct(p: Position, equity: Decimal) -> Decimal:
    """Per-position risk % of equity (0 if not AT_RISK or equity ≤ 0)."""
    if equity <= 0:
        return Decimal("0")
    return at_risk_loss(p) / equity * Decimal("100")


def has_no_stop(p: Position) -> bool:
    return p.sl is None


__all__ = [
    "Classification",
    "Position",
    "drawdown_to_sl",
    "cash_at_risk",
    "at_risk_loss",
    "position_risk_pct",
    "has_no_stop",
]
