"""Tests for ``cfd_skills.price_action.liquidity`` — BSL/SSL pools."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from cfd_skills.price_action.liquidity import LiquidityPool, derive_liquidity_pools
from cfd_skills.price_action.pivots import Pivot


def _piv(i: int, price: str, kind: str) -> Pivot:
    return Pivot(
        index=i,
        time_utc=datetime(2026, 4, 1, tzinfo=timezone.utc),
        price=Decimal(price),
        kind=kind,  # type: ignore[arg-type]
    )


def test_derive_liquidity_pools_unswept_bsl_and_ssl() -> None:
    pivots = [
        _piv(0, "100", "swing_low"),
        _piv(2, "110", "swing_high"),
        _piv(4, "105", "swing_low"),
        _piv(6, "108", "swing_high"),
    ]
    pools = derive_liquidity_pools(
        pivots, tf="H4",
        max_subsequent_high=Decimal("109"),
        max_subsequent_low=Decimal("104"),
    )
    bsl = next(p for p in pools if p.type == "BSL" and p.price == Decimal("110"))
    ssl = next(p for p in pools if p.type == "SSL" and p.price == Decimal("100"))
    assert bsl.swept is False
    assert ssl.swept is False


def test_derive_liquidity_pools_swept_bsl() -> None:
    pivots = [_piv(2, "110", "swing_high")]
    pools = derive_liquidity_pools(
        pivots, tf="H4",
        max_subsequent_high=Decimal("110.5"),
        max_subsequent_low=Decimal("99"),
    )
    bsl = next(p for p in pools if p.type == "BSL")
    assert bsl.swept is True


def test_derive_liquidity_pools_empty() -> None:
    assert derive_liquidity_pools(
        [], tf="H4",
        max_subsequent_high=Decimal("100"),
        max_subsequent_low=Decimal("99"),
    ) == []
