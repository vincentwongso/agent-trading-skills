"""journal_stats: counts, win-rate, R-multiple, breakdowns, swing subset."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from cfd_skills.journal_io import write_open
from cfd_skills.journal_stats import (
    by_setup_type,
    by_side,
    by_symbol,
    compute_summary,
    swing_subset,
)


def _entry(**overrides) -> dict:
    """Minimal resolved-entry dict for stats tests (skips schema bookkeeping)."""
    base = {
        "uuid": overrides.get("uuid", "u-1"),
        "type": "open",
        "schema_version": 1,
        "symbol": "UKOIL",
        "side": "buy",
        "volume": "1.0",
        "entry_price": "75",
        "exit_price": "78",
        "entry_time": "2026-04-29T07:30:00+00:00",
        "exit_time": "2026-05-02T15:45:00+00:00",
        "original_stop_distance_points": 80,
        "original_risk_amount": "100",
        "realized_pnl": "200",
        "swap_accrued": "50",
        "commission": "-5",
        "setup_type": "swap-harvest-long",
        "rationale": "x",
        "risk_classification_at_close": "LOCKED_PROFIT",
        "outcome_notes": None,
    }
    base.update(overrides)
    return base


# --- compute_summary ------------------------------------------------------


def test_empty_returns_zero_summary():
    s = compute_summary([])
    assert s.count == 0
    assert s.total_pnl == Decimal("0")
    assert s.win_rate == Decimal("0")


def test_single_winning_trade():
    s = compute_summary([_entry()])
    assert s.count == 1
    assert s.win_count == 1
    assert s.loss_count == 0
    assert s.win_rate == Decimal("100")
    # net = 200 + 50 - 5 = 245
    assert s.total_pnl == Decimal("245")
    assert s.realized_pnl_total == Decimal("200")
    assert s.swap_pnl_total == Decimal("50")
    assert s.commission_total == Decimal("-5")
    # R = 245 / 100 = 2.45
    assert s.avg_r_multiple == Decimal("2.45")
    assert s.expectancy_per_trade == Decimal("245")


def test_loss_counted_correctly():
    e = _entry(realized_pnl="-150", swap_accrued="0", commission="-5")
    s = compute_summary([e])
    assert s.loss_count == 1
    assert s.win_count == 0
    assert s.win_rate == Decimal("0")
    assert s.total_pnl == Decimal("-155")


def test_breakeven_excluded_from_win_rate():
    breakeven = _entry(realized_pnl="0", swap_accrued="0", commission="0",
                       uuid="u-1")
    win = _entry(uuid="u-2")  # 245 net
    s = compute_summary([breakeven, win])
    assert s.count == 2
    assert s.breakeven_count == 1
    assert s.win_count == 1
    assert s.loss_count == 0
    # win_rate = 1/(1+0) = 100% — breakeven doesn't dilute it.
    assert s.win_rate == Decimal("100")


def test_mixed_outcomes_compute_win_rate():
    entries = [
        _entry(uuid="u-1", realized_pnl="100", swap_accrued="0", commission="-5"),  # +95 win
        _entry(uuid="u-2", realized_pnl="-100", swap_accrued="0", commission="-5"),  # -105 loss
        _entry(uuid="u-3", realized_pnl="50", swap_accrued="0", commission="-5"),    # +45 win
    ]
    s = compute_summary(entries)
    # 2 wins, 1 loss → 66.67%
    assert s.count == 3
    assert s.win_count == 2
    assert s.loss_count == 1
    assert Decimal("66.66") < s.win_rate < Decimal("66.67")


def test_avg_r_multiple_uses_each_entrys_own_risk():
    entries = [
        _entry(uuid="u-1", realized_pnl="100", swap_accrued="0", commission="0",
               original_risk_amount="50"),    # net=100, R=2.0
        _entry(uuid="u-2", realized_pnl="-30", swap_accrued="0", commission="0",
               original_risk_amount="30"),    # net=-30, R=-1.0
    ]
    s = compute_summary(entries)
    # Mean R = (2.0 + -1.0) / 2 = 0.5
    assert s.avg_r_multiple == Decimal("0.5")


def test_swap_pnl_breakout_when_directional_negative():
    # Exit at small loss but kept by carry.
    e = _entry(realized_pnl="-50", swap_accrued="200", commission="-5")
    s = compute_summary([e])
    # Net = -50 + 200 - 5 = 145 → wins despite negative directional
    assert s.win_count == 1
    assert s.swap_pnl_total == Decimal("200")
    assert s.realized_pnl_total == Decimal("-50")


# --- groupings -----------------------------------------------------------


def test_by_setup_type_groups_correctly():
    entries = [
        _entry(uuid="u-1", setup_type="pullback-long", realized_pnl="100",
               swap_accrued="0", commission="0"),
        _entry(uuid="u-2", setup_type="pullback-long", realized_pnl="-50",
               swap_accrued="0", commission="0"),
        _entry(uuid="u-3", setup_type="swap-harvest-long", realized_pnl="0",
               swap_accrued="500", commission="-10"),
    ]
    grouped = by_setup_type(entries)
    assert set(grouped.keys()) == {"pullback-long", "swap-harvest-long"}
    assert grouped["pullback-long"].count == 2
    assert grouped["pullback-long"].total_pnl == Decimal("50")
    assert grouped["swap-harvest-long"].total_pnl == Decimal("490")


def test_by_symbol_groups_correctly():
    entries = [
        _entry(uuid="u-1", symbol="UKOIL"),
        _entry(uuid="u-2", symbol="XAUUSD"),
        _entry(uuid="u-3", symbol="UKOIL"),
    ]
    grouped = by_symbol(entries)
    assert grouped["UKOIL"].count == 2
    assert grouped["XAUUSD"].count == 1


def test_by_side_groups_buy_and_sell():
    entries = [
        _entry(uuid="u-1", side="buy", realized_pnl="100",
               swap_accrued="0", commission="0"),
        _entry(uuid="u-2", side="sell", realized_pnl="50",
               swap_accrued="0", commission="0"),
    ]
    grouped = by_side(entries)
    assert grouped["buy"].count == 1
    assert grouped["sell"].count == 1


def test_by_setup_type_handles_missing_tag():
    e = _entry(setup_type=None)
    grouped = by_setup_type([e])
    assert "(untagged)" in grouped


# --- swing_subset --------------------------------------------------------


def test_swing_subset_picks_carry_driven_trades():
    entries = [
        # Pure swap trade — qualifies.
        _entry(uuid="u-1", realized_pnl="0", swap_accrued="500", commission="-5"),
        # Big swap relative to small directional — qualifies (50 / 100 = 50% > 20%).
        _entry(uuid="u-2", realized_pnl="100", swap_accrued="50", commission="0"),
        # Small swap relative to big directional — disqualifies (50 / 1000 = 5%).
        _entry(uuid="u-3", realized_pnl="1000", swap_accrued="50", commission="0"),
        # Zero swap — never a swing trade.
        _entry(uuid="u-4", realized_pnl="200", swap_accrued="0", commission="0"),
    ]
    out = swing_subset(entries)
    uuids = {e["uuid"] for e in out}
    assert uuids == {"u-1", "u-2"}


def test_swing_subset_threshold_overridable():
    e = _entry(realized_pnl="100", swap_accrued="50", commission="0")
    # Default threshold 0.2 includes (50 > 20). At threshold 0.6, it shouldn't.
    assert e in swing_subset([e])
    assert e not in swing_subset([e], swap_dominance_threshold=Decimal("0.6"))


# --- end-to-end with real journal_io -------------------------------------


def test_summary_over_journal_file(tmp_path):
    journal = tmp_path / "j.jsonl"
    write_open(
        journal,
        symbol="UKOIL", side="buy", volume="1.0",
        entry_price="75.42", exit_price="78.10",
        entry_time=datetime(2026, 4, 29, 7, 30, tzinfo=timezone.utc),
        exit_time=datetime(2026, 5, 2, 15, 45, tzinfo=timezone.utc),
        original_stop_distance_points=80,
        original_risk_amount="80.00",
        realized_pnl="268.00", swap_accrued="375.00", commission="-7.50",
        setup_type="swap-harvest-long",
        rationale="Long carry play.",
        risk_classification_at_close="LOCKED_PROFIT",
    )
    from cfd_skills.journal_io import read_resolved
    entries = read_resolved(journal)
    s = compute_summary(entries)
    assert s.count == 1
    # net = 268 + 375 - 7.50 = 635.50
    assert s.total_pnl == Decimal("635.50")
    # R = 635.50 / 80 = 7.94375
    assert s.avg_r_multiple == Decimal("7.94375")
