"""Output schema for cfd-price-action scans."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

from cfd_skills.price_action.fvg import FVG
from cfd_skills.price_action.liquidity import LiquidityPool
from cfd_skills.price_action.order_block import OrderBlock
from cfd_skills.price_action.pivots import Pivot
from cfd_skills.price_action.structure import MTFAlignment, RegimeKind, SRLevel


SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class EmaStackSnapshot:
    ema21: Optional[Decimal]
    ema50: Optional[Decimal]
    aligned: bool
    direction: str


@dataclass(frozen=True)
class ScanResult:
    schema_version: str
    symbol: str
    mode: str
    timeframes: tuple[str, ...]
    as_of: datetime
    current_price: Decimal
    regime: dict[str, RegimeKind]
    mtf_alignment: MTFAlignment
    pivots_by_tf: dict[str, list[Pivot]]
    sr_levels: list[SRLevel]
    fvgs: list[FVG]
    order_blocks: list[OrderBlock]
    liquidity_pools: list[LiquidityPool]
    ema_stack: dict[str, EmaStackSnapshot]
    setups: list[dict]
    selected_setup_id: Optional[str]
    selection_rationale: Optional[str]
    warnings: list[str]
    recent_bars_window: dict[str, list[dict]]


__all__ = ["SCHEMA_VERSION", "EmaStackSnapshot", "ScanResult"]
