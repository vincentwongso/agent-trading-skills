"""Integration tests for the price_action.scan orchestrator."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading_agent_skills.price_action.scan import ScanInput, scan


def _bar_blob(i: int, h: str, l: str, o: str, c: str) -> dict:
    t = datetime(2026, 4, 1, tzinfo=timezone.utc) + timedelta(hours=i)
    return {
        "time": t.isoformat(),
        "open": o, "high": h, "low": l, "close": c,
        "volume": 0,
    }


def _trend_up_bars(n: int = 80) -> list[dict]:
    out: list[dict] = []
    price = Decimal("100")
    for i in range(n):
        price += Decimal("0.50")
        out.append(_bar_blob(
            i,
            h=str(price + Decimal("0.30")),
            l=str(price - Decimal("0.30")),
            o=str(price - Decimal("0.10")),
            c=str(price),
        ))
    return out


def test_scan_returns_schema_compliant_result() -> None:
    bars = _trend_up_bars()
    inp = ScanInput(
        symbol="XAUUSD",
        mode="swing",
        timeframes=("D1", "H4", "H1"),
        rates_by_tf={"D1": bars, "H4": bars, "H1": bars},
        current_price=Decimal(bars[-1]["close"]),
        tick_size=Decimal("0.01"),
        digits=2,
        as_of=datetime(2026, 4, 5, tzinfo=timezone.utc),
    )
    result = scan(inp)
    assert result.symbol == "XAUUSD"
    assert result.mode == "swing"
    assert "H4" in result.regime
    assert isinstance(result.setups, list)
    assert result.selected_setup_id is None
    assert result.selection_rationale is None
    assert "H1" in result.recent_bars_window


def test_scan_empty_setups_yields_warning() -> None:
    flat = [_bar_blob(i, "100.5", "99.5", "100", "100") for i in range(80)]
    inp = ScanInput(
        symbol="XAUUSD",
        mode="swing",
        timeframes=("D1", "H4", "H1"),
        rates_by_tf={"D1": flat, "H4": flat, "H1": flat},
        current_price=Decimal("100"),
        tick_size=Decimal("0.01"),
        digits=2,
        as_of=datetime(2026, 4, 5, tzinfo=timezone.utc),
    )
    result = scan(inp)
    if not result.setups:
        assert "no_clean_setup" in result.warnings


def test_scan_sparse_bars_warning() -> None:
    short_bars = [_bar_blob(i, "101", "100", "100", "100.5") for i in range(10)]
    inp = ScanInput(
        symbol="XAUUSD",
        mode="swing",
        timeframes=("D1", "H4", "H1"),
        rates_by_tf={"D1": short_bars, "H4": short_bars, "H1": short_bars},
        current_price=Decimal("100"),
        tick_size=Decimal("0.01"),
        digits=2,
        as_of=datetime(2026, 4, 5, tzinfo=timezone.utc),
    )
    result = scan(inp)
    assert any(w.startswith("sparse_bars_") for w in result.warnings)


def test_scan_caps_setups_at_max() -> None:
    bars = _trend_up_bars()
    inp = ScanInput(
        symbol="XAUUSD",
        mode="swing",
        timeframes=("D1", "H4", "H1"),
        rates_by_tf={"D1": bars, "H4": bars, "H1": bars},
        current_price=Decimal(bars[-1]["close"]),
        tick_size=Decimal("0.01"),
        digits=2,
        as_of=datetime(2026, 4, 5, tzinfo=timezone.utc),
        max_setups=2,
    )
    result = scan(inp)
    assert len(result.setups) <= 2
