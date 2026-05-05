"""Macro economic context from AlphaVantage economic indicator APIs.

Extracts latest + previous readings for each indicator, computes directional
change, and detects staleness based on expected update cadence. All values
are Decimal-typed via ``D()`` — no floats cross this boundary.

This module is pure — no I/O. The agent fetches AV MCP tool outputs and
passes the raw response data in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from trading_agent_skills.decimal_io import D


_EXPECTED_CADENCE: dict[str, timedelta] = {
    "TREASURY_YIELD": timedelta(days=3),
    "FEDERAL_FUNDS_RATE": timedelta(days=3),
    "CPI": timedelta(days=45),
    "INFLATION": timedelta(days=400),
    "UNEMPLOYMENT": timedelta(days=45),
    "NONFARM_PAYROLL": timedelta(days=45),
    "REAL_GDP": timedelta(days=120),
    "RETAIL_SALES": timedelta(days=45),
    "DURABLES": timedelta(days=45),
}


@dataclass(frozen=True)
class MacroReading:
    name: str
    latest_value: Decimal
    latest_date: str
    previous_value: Decimal
    previous_date: str
    direction: str  # "rising" / "falling" / "flat"


@dataclass(frozen=True)
class MacroContext:
    readings: tuple[MacroReading, ...]
    staleness_flags: tuple[str, ...]


def _parse_reading(
    name: str,
    data_points: list[dict[str, str]],
) -> MacroReading | None:
    valid = [dp for dp in data_points if dp.get("value") and dp["value"] != "."]
    if len(valid) < 2:
        return None
    latest = valid[0]
    previous = valid[1]
    latest_val = D(latest["value"])
    prev_val = D(previous["value"])
    if latest_val > prev_val:
        direction = "rising"
    elif latest_val < prev_val:
        direction = "falling"
    else:
        direction = "flat"
    return MacroReading(
        name=name,
        latest_value=latest_val,
        latest_date=latest["date"],
        previous_value=prev_val,
        previous_date=previous["date"],
        direction=direction,
    )


def build_macro_context(
    indicators: dict[str, list[dict[str, str]]],
    *,
    reference_date: date | None = None,
) -> MacroContext:
    ref = reference_date or date.today()
    readings: list[MacroReading] = []
    stale: list[str] = []

    for name, data_points in indicators.items():
        reading = _parse_reading(name, data_points)
        if reading is None:
            stale.append(name)
            continue
        readings.append(reading)
        cadence = _EXPECTED_CADENCE.get(name)
        if cadence and ref - date.fromisoformat(reading.latest_date) > cadence:
            stale.append(name)

    return MacroContext(
        readings=tuple(readings),
        staleness_flags=tuple(stale),
    )


__all__ = ["MacroReading", "MacroContext", "build_macro_context"]
