import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trading_agent_skills.decision_log import (
    ALLOWED_KINDS,
    ALLOWED_EXEC_STATUSES,
    DecisionSchemaError,
    filter_decisions,
    reconcile_decisions,
    write_intent,
    write_outcome,
)


def test_write_intent_open_appends_record(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    write_intent(
        path,
        kind="open",
        symbol="XAUUSD.z",
        ticket=None,
        setup_type="price_action:pin_bar",
        reasoning="Pullback to 2380, pin bar rejection on H1.",
        skills_used=["price-action", "pre-trade-checklist", "position-sizer"],
        guardian_status="CLEAR",
        checklist_verdict="PASS",
        execution={
            "side": "BUY",
            "volume": "0.08",
            "entry_price": "2380.50",
            "sl": "2375.00",
            "tp": "2395.00",
        },
        charter_version=3,
        tick_id="2026-04-30T22:00:00Z",
    )
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["kind"] == "open"
    assert rec["symbol"] == "XAUUSD.z"
    assert rec["execution"]["execution_status"] == "pending"
    assert rec["execution"]["volume"] == "0.08"  # string, not float
    assert rec["charter_version"] == 3
    assert rec["tick_id"] == "2026-04-30T22:00:00Z"


def test_write_intent_skip_no_execution(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    write_intent(
        path,
        kind="skip",
        symbol="EURUSD.z",
        ticket=None,
        setup_type="price_action:fvg_fill",
        reasoning="Spread 1.8x baseline, skipped.",
        skills_used=["price-action", "pre-trade-checklist"],
        guardian_status="CAUTION",
        checklist_verdict="BLOCK",
        execution=None,
        charter_version=3,
        tick_id="2026-04-30T22:00:00Z",
    )
    rec = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert rec["kind"] == "skip"
    assert rec["execution"] is None


def test_write_intent_rejects_unknown_kind(tmp_path: Path) -> None:
    with pytest.raises(DecisionSchemaError, match="kind"):
        write_intent(
            tmp_path / "d.jsonl",
            kind="explode",
            symbol="X",
            ticket=None,
            setup_type="x",
            reasoning="r",
            skills_used=[],
            guardian_status="CLEAR",
            checklist_verdict=None,
            execution=None,
            charter_version=1,
            tick_id="2026-04-30T22:00:00Z",
        )


def test_write_intent_rejects_open_without_execution(tmp_path: Path) -> None:
    with pytest.raises(DecisionSchemaError, match="execution"):
        write_intent(
            tmp_path / "d.jsonl",
            kind="open",
            symbol="X",
            ticket=None,
            setup_type="x",
            reasoning="r",
            skills_used=[],
            guardian_status="CLEAR",
            checklist_verdict="PASS",
            execution=None,
            charter_version=1,
            tick_id="2026-04-30T22:00:00Z",
        )


def test_write_intent_rejects_open_without_setup_type(tmp_path: Path) -> None:
    with pytest.raises(DecisionSchemaError, match="setup_type"):
        write_intent(
            tmp_path / "d.jsonl",
            kind="open",
            symbol="X",
            ticket=None,
            setup_type="",
            reasoning="r",
            skills_used=[],
            guardian_status="CLEAR",
            checklist_verdict="PASS",
            execution={
                "side": "BUY", "volume": "0.1", "entry_price": "1.0",
                "sl": "0.99", "tp": "1.02",
            },
            charter_version=1,
            tick_id="2026-04-30T22:00:00Z",
        )


def test_write_intent_rejects_naive_tick_id(tmp_path: Path) -> None:
    with pytest.raises(DecisionSchemaError, match="tick_id"):
        write_intent(
            tmp_path / "d.jsonl",
            kind="skip",
            symbol="X",
            ticket=None,
            setup_type="x",
            reasoning="r",
            skills_used=[],
            guardian_status="CLEAR",
            checklist_verdict="BLOCK",
            execution=None,
            charter_version=1,
            tick_id="2026-04-30T22:00:00",  # missing Z / +00:00
        )


def test_write_intent_rejects_volume_as_float(tmp_path: Path) -> None:
    with pytest.raises(DecisionSchemaError, match="volume"):
        write_intent(
            tmp_path / "d.jsonl",
            kind="open",
            symbol="X",
            ticket=None,
            setup_type="x",
            reasoning="r",
            skills_used=[],
            guardian_status="CLEAR",
            checklist_verdict="PASS",
            execution={
                "side": "BUY", "volume": 0.08, "entry_price": "1.0",
                "sl": "0.99", "tp": "1.02",
            },
            charter_version=1,
            tick_id="2026-04-30T22:00:00Z",
        )


def test_allowed_constants() -> None:
    assert ALLOWED_KINDS == ("open", "modify", "close", "skip", "mode_change")
    assert "pending" in ALLOWED_EXEC_STATUSES
    assert "filled" in ALLOWED_EXEC_STATUSES
    assert "rejected" in ALLOWED_EXEC_STATUSES
    assert "broker_error" in ALLOWED_EXEC_STATUSES


def test_outcome_pending_to_filled(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    write_intent(
        path, kind="open", symbol="XAUUSD.z", ticket=None,
        setup_type="price_action:pin_bar", reasoning="r", skills_used=[],
        guardian_status="CLEAR", checklist_verdict="PASS",
        execution={"side": "BUY", "volume": "0.08",
                   "entry_price": "2380.50", "sl": "2375.00", "tp": "2395.00"},
        charter_version=3, tick_id="2026-04-30T22:00:00Z",
    )
    write_outcome(
        path, tick_id="2026-04-30T22:00:00Z", kind="open", symbol="XAUUSD.z",
        execution_status="filled", ticket=99999,
        actual_fill_price="2380.55", failure_reason=None,
    )
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    intent = json.loads(lines[0])
    outcome = json.loads(lines[1])
    assert intent["execution"]["execution_status"] == "pending"
    assert outcome["execution"]["execution_status"] == "filled"
    assert outcome["ticket"] == 99999


def test_outcome_rejected_with_reason(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    write_intent(
        path, kind="open", symbol="X", ticket=None, setup_type="x",
        reasoning="r", skills_used=[], guardian_status="CLEAR", checklist_verdict="PASS",
        execution={"side": "BUY", "volume": "0.1", "entry_price": "1.0",
                   "sl": "0.99", "tp": "1.02"},
        charter_version=1, tick_id="2026-04-30T22:00:00Z",
    )
    write_outcome(
        path, tick_id="2026-04-30T22:00:00Z", kind="open", symbol="X",
        execution_status="rejected", ticket=None, actual_fill_price=None,
        failure_reason="market closed",
    )
    outcome = json.loads(path.read_text(encoding="utf-8").splitlines()[1])
    assert outcome["execution"]["execution_status"] == "rejected"
    assert outcome["execution"]["failure_reason"] == "market closed"


def test_outcome_rejects_invalid_status(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    with pytest.raises(DecisionSchemaError, match="execution_status"):
        write_outcome(
            path, tick_id="2026-04-30T22:00:00Z", kind="open", symbol="X",
            execution_status="totally_filled", ticket=1,
            actual_fill_price=None, failure_reason=None,
        )


def test_reconcile_picks_latest_outcome(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    write_intent(
        path, kind="open", symbol="XAUUSD.z", ticket=None, setup_type="x",
        reasoning="why", skills_used=[], guardian_status="CLEAR", checklist_verdict="PASS",
        execution={"side": "BUY", "volume": "0.1", "entry_price": "1.0",
                   "sl": "0.99", "tp": "1.02"},
        charter_version=1, tick_id="2026-04-30T22:00:00Z",
    )
    write_outcome(
        path, tick_id="2026-04-30T22:00:00Z", kind="open", symbol="XAUUSD.z",
        execution_status="filled", ticket=42, actual_fill_price="1.0001",
        failure_reason=None,
    )
    reconciled = list(reconcile_decisions(path))
    assert len(reconciled) == 1
    rec = reconciled[0]
    assert rec["reasoning"] == "why"
    assert rec["execution"]["execution_status"] == "filled"
    assert rec["ticket"] == 42


def test_reconcile_orphan_intent_stays_pending(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    write_intent(
        path, kind="open", symbol="X", ticket=None, setup_type="x",
        reasoning="r", skills_used=[], guardian_status="CLEAR", checklist_verdict="PASS",
        execution={"side": "BUY", "volume": "0.1", "entry_price": "1.0",
                   "sl": "0.99", "tp": "1.02"},
        charter_version=1, tick_id="2026-04-30T22:00:00Z",
    )
    reconciled = list(reconcile_decisions(path))
    assert reconciled[0]["execution"]["execution_status"] == "pending"


def test_reconcile_first_intent_wins_on_duplicate(tmp_path: Path) -> None:
    """Duplicate intents on same (tick_id, kind, symbol) — first wins, retries don't displace."""
    path = tmp_path / "decisions.jsonl"
    write_intent(
        path, kind="open", symbol="X", ticket=None, setup_type="x",
        reasoning="first reason", skills_used=[],
        guardian_status="CLEAR", checklist_verdict="PASS",
        execution={"side": "BUY", "volume": "0.1", "entry_price": "1.0",
                   "sl": "0.99", "tp": "1.02"},
        charter_version=1, tick_id="2026-04-30T22:00:00Z",
    )
    # Same tick_id/kind/symbol, different reasoning (retry)
    write_intent(
        path, kind="open", symbol="X", ticket=None, setup_type="x",
        reasoning="retry reason", skills_used=[],
        guardian_status="CLEAR", checklist_verdict="PASS",
        execution={"side": "BUY", "volume": "0.1", "entry_price": "1.0",
                   "sl": "0.99", "tp": "1.02"},
        charter_version=1, tick_id="2026-04-30T22:00:00Z",
    )
    reconciled = list(reconcile_decisions(path))
    assert len(reconciled) == 1
    assert reconciled[0]["reasoning"] == "first reason"


def test_reconcile_handles_z_suffix_in_ts(tmp_path: Path) -> None:
    """Reconciler must compare ts via parsed datetime, not lexicographic.

    Manually craft two outcome records with different ts formats (Z vs +00:00)
    that lexicographically would sort wrong but chronologically are unambiguous.
    """
    import json
    path = tmp_path / "decisions.jsonl"
    # Intent
    write_intent(
        path, kind="open", symbol="X", ticket=None, setup_type="x",
        reasoning="r", skills_used=[],
        guardian_status="CLEAR", checklist_verdict="PASS",
        execution={"side": "BUY", "volume": "0.1", "entry_price": "1.0",
                   "sl": "0.99", "tp": "1.02"},
        charter_version=1, tick_id="2026-04-30T22:00:00Z",
    )
    # Older outcome with Z suffix
    older = {
        "schema_version": 1,
        "ts": "2026-04-30T22:00:01+00:00",  # earlier
        "kind": "open", "symbol": "X", "ticket": 1,
        "execution": {"execution_status": "rejected"},
        "tick_id": "2026-04-30T22:00:00Z",
        "is_outcome": True,
    }
    # Newer outcome
    newer = {
        "schema_version": 1,
        "ts": "2026-04-30T22:00:02+00:00",  # later by 1 second
        "kind": "open", "symbol": "X", "ticket": 99999,
        "execution": {"execution_status": "filled"},
        "tick_id": "2026-04-30T22:00:00Z",
        "is_outcome": True,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(older) + "\n")
        f.write(json.dumps(newer) + "\n")
    reconciled = list(reconcile_decisions(path))
    assert len(reconciled) == 1
    assert reconciled[0]["execution"]["execution_status"] == "filled"
    assert reconciled[0]["ticket"] == 99999


def _seed_decisions(path: Path) -> None:
    base = datetime(2026, 4, 29, 22, 0, 0, tzinfo=timezone.utc)
    for i, (kind, sym, setup) in enumerate([
        ("open", "XAUUSD.z", "price_action:pin_bar"),
        ("skip", "EURUSD.z", "price_action:fvg_fill"),
        ("close", "XAUUSD.z", None),
    ]):
        tick = (base + timedelta(days=i)).isoformat().replace("+00:00", "Z")
        kwargs = dict(
            kind=kind, symbol=sym, ticket=None if kind == "skip" else 100 + i,
            setup_type=setup, reasoning=f"r{i}", skills_used=[],
            guardian_status="CLEAR",
            checklist_verdict="PASS" if kind == "open" else ("BLOCK" if kind == "skip" else None),
            charter_version=1, tick_id=tick,
        )
        if kind == "skip":
            write_intent(path, execution=None, **kwargs)
        else:
            write_intent(
                path,
                execution={"side": "BUY", "volume": "0.1", "entry_price": "1.0",
                           "sl": "0.99", "tp": "1.02"},
                **kwargs,
            )


def test_filter_by_kind(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    _seed_decisions(path)
    skips = list(filter_decisions(path, kind="skip"))
    assert len(skips) == 1
    assert skips[0]["symbol"] == "EURUSD.z"


def test_filter_by_symbol(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    _seed_decisions(path)
    xau = list(filter_decisions(path, symbol="XAUUSD.z"))
    assert len(xau) == 2
    assert {r["kind"] for r in xau} == {"open", "close"}


def test_filter_since_filters_old(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    _seed_decisions(path)
    cutoff = datetime(2026, 4, 30, 0, 0, 0, tzinfo=timezone.utc)
    recent = list(filter_decisions(path, since=cutoff))
    # Three records seeded at 2026-04-29, 2026-04-30, 2026-05-01 — only 04-30 and 05-01 pass
    assert len(recent) == 2


def test_filter_combines_predicates(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    _seed_decisions(path)
    cutoff = datetime(2026, 4, 30, 0, 0, 0, tzinfo=timezone.utc)
    recent_xau = list(filter_decisions(path, since=cutoff, symbol="XAUUSD.z"))
    assert len(recent_xau) == 1
    assert recent_xau[0]["kind"] == "close"
