import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from trading_agent_skills.account_paths import resolve_account_paths
from trading_agent_skills.decision_log import write_intent
from trading_agent_skills.journal_io import write_open
from trading_agent_skills.charter_io import parse_charter
from trading_agent_skills.strategy_review import (
    PROPOSABLE_FIELDS,
    apply_proposal,
    build_proposal_skeleton,
    compute_decision_summary,
    compute_performance_summary,
    compute_setup_breakdown,
    validate_proposal_diff,
)


_VALID_CHARTER_TEXT = """\
mode: demo
account_id: 12345678
heartbeat: 1h
hard_caps:
  per_trade_risk_pct: 1.0
  daily_loss_pct: 5.0
  max_concurrent_positions: 3
charter_version: 1
created_at: 2026-04-30T14:00:00+10:00
created_account_balance: 10000.00
trading_style: day
sessions_allowed: []
instruments: []
allowed_setups: []
notes: ""
"""


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


def test_setup_breakdown_per_label(tmp_path: Path) -> None:
    paths = resolve_account_paths(account_id="12345678", base=tmp_path)
    paths.ensure_dirs()
    _seed_journal(paths.journal, n_wins=3, n_losses=2)
    bd = compute_setup_breakdown(
        paths,
        since=datetime(2026, 4, 1, tzinfo=timezone.utc),
        until=datetime(2026, 5, 10, tzinfo=timezone.utc),
    )
    pin = next(b for b in bd if b["setup_type"] == "price_action:pin_bar")
    fvg = next(b for b in bd if b["setup_type"] == "price_action:fvg_fill")
    assert pin["wins"] == 3
    assert pin["losses"] == 0
    assert fvg["wins"] == 0
    assert fvg["losses"] == 2


def test_decision_summary_groups_skip_reasons(tmp_path: Path) -> None:
    paths = resolve_account_paths(account_id="12345678", base=tmp_path)
    paths.ensure_dirs()
    skip_reasons = ["spread_too_wide", "spread_too_wide", "guardian_caution"]
    base = datetime(2026, 4, 29, 22, 0, 0, tzinfo=timezone.utc)
    for i, reason in enumerate(skip_reasons):
        write_intent(
            paths.decisions, kind="skip", symbol="X", ticket=None,
            setup_type="price_action:pin_bar", reasoning=reason,
            skills_used=[], guardian_status="CLEAR", checklist_verdict="BLOCK",
            execution=None, charter_version=1,
            tick_id=(base + timedelta(days=i)).isoformat().replace("+00:00", "Z"),
        )
    summary = compute_decision_summary(
        paths,
        since=datetime(2026, 4, 1, tzinfo=timezone.utc),
        until=datetime(2026, 5, 10, tzinfo=timezone.utc),
    )
    assert summary["total_decisions"] == 3
    assert summary["skips"] == 3
    assert summary["entries"] == 0
    assert summary["top_skip_reasons"][0] == ("spread_too_wide", 2)


def test_proposable_fields_excludes_locked() -> None:
    assert "mode" not in PROPOSABLE_FIELDS
    assert "account_id" not in PROPOSABLE_FIELDS
    assert "created_at" not in PROPOSABLE_FIELDS
    assert "created_account_balance" not in PROPOSABLE_FIELDS
    assert "charter_version" not in PROPOSABLE_FIELDS
    assert "per_trade_risk_pct" in PROPOSABLE_FIELDS
    assert "instruments" in PROPOSABLE_FIELDS
    assert "allowed_setups" in PROPOSABLE_FIELDS


def test_validate_proposal_rejects_locked_field_change() -> None:
    bad = {"mode": "live"}
    with pytest.raises(ValueError, match="locked"):
        validate_proposal_diff(bad)


def test_validate_proposal_accepts_proposable_fields() -> None:
    ok = {"per_trade_risk_pct": 0.8, "allowed_setups": ["price_action:pin_bar"]}
    validate_proposal_diff(ok)  # no exception


def test_build_proposal_skeleton_emits_markdown(tmp_path: Path) -> None:
    paths = resolve_account_paths(account_id="12345678", base=tmp_path)
    paths.ensure_dirs()
    paths.charter.write_text(_VALID_CHARTER_TEXT, encoding="utf-8")
    paths.journal.touch()
    md = build_proposal_skeleton(
        paths,
        since=datetime(2026, 4, 25, tzinfo=timezone.utc),
        until=datetime(2026, 5, 2, tzinfo=timezone.utc),
    )
    assert "Strategy review" in md
    assert "Performance summary" in md
    assert "Decision-log analysis" in md
    assert "Charter diff proposal" in md
    assert "Reply with" in md


def test_apply_proposal_increments_version_and_archives(tmp_path: Path) -> None:
    paths = resolve_account_paths(account_id="12345678", base=tmp_path)
    paths.ensure_dirs()
    paths.charter.write_text(_VALID_CHARTER_TEXT, encoding="utf-8")
    new_charter = apply_proposal(
        paths,
        approved_changes={"per_trade_risk_pct": 0.8},
    )
    assert new_charter.charter_version == 2
    assert new_charter.hard_caps.per_trade_risk_pct == 0.8
    assert (paths.charter_versions / "v1.md").is_file()
    assert "per_trade_risk_pct: 0.8" in paths.charter.read_text(encoding="utf-8")


def test_apply_proposal_rejects_locked_field(tmp_path: Path) -> None:
    paths = resolve_account_paths(account_id="12345678", base=tmp_path)
    paths.ensure_dirs()
    paths.charter.write_text(_VALID_CHARTER_TEXT, encoding="utf-8")
    with pytest.raises(ValueError, match="locked"):
        apply_proposal(paths, approved_changes={"mode": "live"})


def test_apply_proposal_rejects_out_of_bounds_risk(tmp_path: Path) -> None:
    """apply_proposal must round-trip-validate to reject malformed values."""
    paths = resolve_account_paths(account_id="12345678", base=tmp_path)
    paths.ensure_dirs()
    paths.charter.write_text(_VALID_CHARTER_TEXT, encoding="utf-8")
    with pytest.raises(ValueError, match="per_trade_risk_pct"):
        apply_proposal(paths, approved_changes={"per_trade_risk_pct": 99.0})
    # Charter must NOT be written on validation failure
    current = parse_charter(paths.charter.read_text(encoding="utf-8"))
    assert current.charter_version == 1
    assert current.hard_caps.per_trade_risk_pct == 1.0
