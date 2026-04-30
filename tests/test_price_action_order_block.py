"""Tests for ``trading_agent_skills.price_action.order_block`` — OB detection."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading_agent_skills.indicators import Bar
from trading_agent_skills.price_action.order_block import OrderBlock, detect_order_blocks


def _bar(i: int, o: str, h: str, l: str, c: str) -> Bar:
    return Bar(
        time_utc=datetime(2026, 4, 1, tzinfo=timezone.utc) + timedelta(hours=i),
        open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c),
        volume=0,
    )


def test_detect_order_blocks_demand_ob() -> None:
    bars = [
        _bar(0, "100", "101", "99",  "99.5"),
        _bar(1, "99.5", "100", "98", "98.5"),
        _bar(2, "98.5", "103", "98.5", "102.5"),
        _bar(3, "102.5", "104", "102", "103.5"),
        _bar(4, "103.5", "105", "103", "104.5"),
    ]
    obs = detect_order_blocks(
        bars, tf="H1",
        atr=Decimal("1.0"),
        displacement_atr_mult=Decimal("1.5"),
        displacement_lookahead=2,
    )
    demand = [o for o in obs if o.side == "demand"]
    assert len(demand) == 1
    ob = demand[0]
    assert ob.high == Decimal("100")
    assert ob.low == Decimal("98")
    assert ob.retested is False


def test_detect_order_blocks_supply_ob() -> None:
    bars = [
        _bar(0, "100", "101", "99", "100.5"),
        _bar(1, "100.5", "102", "100", "101.5"),
        _bar(2, "101.5", "101.5", "97", "97.5"),
        _bar(3, "97.5", "98", "96", "96.5"),
        _bar(4, "96.5", "97", "95", "95.5"),
    ]
    obs = detect_order_blocks(
        bars, tf="H1",
        atr=Decimal("1.0"),
        displacement_atr_mult=Decimal("1.5"),
        displacement_lookahead=2,
    )
    supply = [o for o in obs if o.side == "supply"]
    assert len(supply) == 1
    ob = supply[0]
    assert ob.high == Decimal("102")
    assert ob.low == Decimal("100")


def test_detect_order_blocks_marks_retested_demand() -> None:
    bars = [
        _bar(0, "100", "101", "99", "99.5"),
        _bar(1, "99.5", "100", "98", "98.5"),
        _bar(2, "98.5", "103", "98.5", "102.5"),
        _bar(3, "102.5", "104", "102", "103.5"),
        _bar(4, "103.5", "104", "99",  "99.5"),
    ]
    obs = detect_order_blocks(
        bars, tf="H1",
        atr=Decimal("1.0"),
        displacement_atr_mult=Decimal("1.5"),
        displacement_lookahead=2,
    )
    demand = next(o for o in obs if o.side == "demand")
    assert demand.retested is True


def test_detect_order_blocks_no_displacement() -> None:
    bars = [
        _bar(0, "100", "100.5", "99.5", "100"),
        _bar(1, "100", "100.5", "99.5", "100"),
        _bar(2, "100", "100.5", "99.5", "100"),
        _bar(3, "100", "100.5", "99.5", "100"),
    ]
    obs = detect_order_blocks(
        bars, tf="H1",
        atr=Decimal("1.0"),
        displacement_atr_mult=Decimal("1.5"),
        displacement_lookahead=2,
    )
    assert obs == []


def test_detect_order_blocks_handles_short_series() -> None:
    assert detect_order_blocks(
        [_bar(0, "100", "101", "99", "100")],
        tf="H1", atr=Decimal("1.0"),
        displacement_atr_mult=Decimal("1.5"),
        displacement_lookahead=2,
    ) == []
