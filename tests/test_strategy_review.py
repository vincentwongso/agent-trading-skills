import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from trading_agent_skills.account_paths import resolve_account_paths
from trading_agent_skills.journal_io import write_open
from trading_agent_skills.strategy_review import compute_performance_summary


def _seed_journal(path: Path, n_wins: int, n_losses: int) -> None:
    base = datetime(2026, 4, 25, 8, 0, 0, tzinfo=timezone.utc)
    for i in range(n_wins):
        write_open(
            path, symbol="XAUUSD.z", side="buy", volume="0.1",
            entry_price="2380.00", exit_price="2390.00",
            entry_time=base + timedelta(days=i),
            exit_time=base + timedelta(days=i, hours=4),
            original_stop_distance_points=50,
            original_risk_amount="100.00", realized_pnl="100.00",
            swap_accrued="0.00", commission="0.00",
            setup_type="price_action:pin_bar", rationale="test",
            risk_classification_at_close="AT_RISK",
        )
    for i in range(n_losses):
        write_open(
            path, symbol="EURUSD.z", side="buy", volume="0.1",
            entry_price="1.0800", exit_price="1.0750",
            entry_time=base + timedelta(days=n_wins + i),
            exit_time=base + timedelta(days=n_wins + i, hours=4),
            original_stop_distance_points=50,
            original_risk_amount="100.00", realized_pnl="-100.00",
            swap_accrued="0.00", commission="0.00",
            setup_type="price_action:fvg_fill", rationale="test",
            risk_classification_at_close="AT_RISK",
        )


def test_perf_summary_counts(tmp_path: Path) -> None:
    paths = resolve_account_paths(account_id="12345678", base=tmp_path)
    paths.ensure_dirs()
    _seed_journal(paths.journal, n_wins=3, n_losses=2)
    summary = compute_performance_summary(
        paths,
        since=datetime(2026, 4, 1, tzinfo=timezone.utc),
        until=datetime(2026, 5, 10, tzinfo=timezone.utc),
    )
    assert summary["trades_closed"] == 5
    assert summary["wins"] == 3
    assert summary["losses"] == 2
    assert summary["win_rate"] == pytest.approx(60.0)
    assert Decimal(summary["realized_pnl"]) == Decimal("100.00")  # 3*100 - 2*100


def test_perf_summary_excludes_outside_window(tmp_path: Path) -> None:
    paths = resolve_account_paths(account_id="12345678", base=tmp_path)
    paths.ensure_dirs()
    _seed_journal(paths.journal, n_wins=1, n_losses=0)
    summary = compute_performance_summary(
        paths,
        since=datetime(2026, 5, 1, tzinfo=timezone.utc),
        until=datetime(2026, 5, 10, tzinfo=timezone.utc),
    )
    assert summary["trades_closed"] == 0


def test_perf_summary_handles_empty_journal(tmp_path: Path) -> None:
    paths = resolve_account_paths(account_id="12345678", base=tmp_path)
    paths.ensure_dirs()
    paths.journal.touch()
    summary = compute_performance_summary(
        paths,
        since=datetime(2026, 4, 1, tzinfo=timezone.utc),
        until=datetime(2026, 5, 10, tzinfo=timezone.utc),
    )
    assert summary["trades_closed"] == 0
    assert summary["win_rate"] is None
