"""End-to-end CLI test for trading-agent-skills-journal subcommands."""

import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trading_agent_skills.cli.journal import main


def _open_payload(**overrides) -> dict:
    base = {
        "symbol": "UKOIL",
        "side": "buy",
        "volume": "1.0",
        "entry_price": "75.42",
        "exit_price": "78.10",
        "entry_time": "2026-04-29T07:30:00+00:00",
        "exit_time": "2026-05-02T15:45:00+00:00",
        "original_stop_distance_points": 80,
        "original_risk_amount": "80.00",
        "realized_pnl": "268.00",
        "swap_accrued": "375.00",
        "commission": "-7.50",
        "setup_type": "swap-harvest-long",
        "rationale": "Long carry play.",
        "risk_classification_at_close": "LOCKED_PROFIT",
    }
    base.update(overrides)
    return base


def _run(monkeypatch, stdin_str: str | None, argv: list[str]) -> tuple[int, str, str]:
    if stdin_str is not None:
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin_str))
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = main(argv)
    return rc, out.getvalue(), err.getvalue()


# --- write ---------------------------------------------------------------


def test_write_appends_and_returns_uuid_with_json_flag(monkeypatch, tmp_path: Path):
    journal = tmp_path / "j.jsonl"
    payload = _open_payload()
    rc, out, err = _run(monkeypatch, json.dumps(payload),
                        ["--journal-path", str(journal), "write", "--json"])
    assert rc == 0, err
    parsed = json.loads(out)
    assert "uuid" in parsed
    assert journal.read_text().count("\n") == 1


def test_write_human_output_default(monkeypatch, tmp_path: Path):
    journal = tmp_path / "j.jsonl"
    rc, out, err = _run(monkeypatch, json.dumps(_open_payload()),
                        ["--journal-path", str(journal), "write"])
    assert rc == 0
    assert "Wrote entry" in out


def test_write_invalid_json_returns_1(monkeypatch, tmp_path: Path):
    journal = tmp_path / "j.jsonl"
    rc, out, err = _run(monkeypatch, "{not json",
                        ["--journal-path", str(journal), "write"])
    assert rc == 1
    assert "invalid JSON" in err


def test_write_schema_error_returns_1(monkeypatch, tmp_path: Path):
    journal = tmp_path / "j.jsonl"
    payload = _open_payload(side="WHAT")
    rc, out, err = _run(monkeypatch, json.dumps(payload),
                        ["--journal-path", str(journal), "write"])
    assert rc == 1
    assert "side" in err


# --- update --------------------------------------------------------------


def test_update_patches_existing_entry(monkeypatch, tmp_path: Path):
    journal = tmp_path / "j.jsonl"
    rc, out, _ = _run(monkeypatch, json.dumps(_open_payload()),
                      ["--journal-path", str(journal), "write", "--json"])
    uid = json.loads(out)["uuid"]
    rc, out, err = _run(
        monkeypatch,
        json.dumps({"uuid": uid, "outcome_notes": "Held longer than planned."}),
        ["--journal-path", str(journal), "update", "--json"],
    )
    assert rc == 0, err
    assert journal.read_text().count("\n") == 2


def test_update_requires_uuid(monkeypatch, tmp_path: Path):
    journal = tmp_path / "j.jsonl"
    rc, out, err = _run(
        monkeypatch, json.dumps({"outcome_notes": "x"}),
        ["--journal-path", str(journal), "update"],
    )
    assert rc == 1
    assert "uuid" in err


# --- query ---------------------------------------------------------------


def test_query_returns_all_entries(monkeypatch, tmp_path: Path):
    journal = tmp_path / "j.jsonl"
    _run(monkeypatch, json.dumps(_open_payload(symbol="UKOIL")),
         ["--journal-path", str(journal), "write"])
    _run(monkeypatch, json.dumps(_open_payload(symbol="XAUUSD")),
         ["--journal-path", str(journal), "write"])
    rc, out, err = _run(monkeypatch, None,
                        ["--journal-path", str(journal), "query"])
    assert rc == 0, err
    parsed = json.loads(out)
    assert parsed["count"] == 2


def test_query_filters_by_symbol(monkeypatch, tmp_path: Path):
    journal = tmp_path / "j.jsonl"
    _run(monkeypatch, json.dumps(_open_payload(symbol="UKOIL")),
         ["--journal-path", str(journal), "write"])
    _run(monkeypatch, json.dumps(_open_payload(symbol="XAUUSD")),
         ["--journal-path", str(journal), "write"])
    rc, out, _ = _run(monkeypatch, None,
                      ["--journal-path", str(journal), "query", "--symbol", "UKOIL"])
    parsed = json.loads(out)
    assert parsed["count"] == 1
    assert parsed["entries"][0]["symbol"] == "UKOIL"


def test_query_swing_only_filters_carry_trades(monkeypatch, tmp_path: Path):
    journal = tmp_path / "j.jsonl"
    # Pure carry trade — qualifies.
    _run(monkeypatch, json.dumps(_open_payload(
        symbol="CARRY", realized_pnl="0", swap_accrued="500", commission="-5")),
        ["--journal-path", str(journal), "write"])
    # Directional trade with no swap — disqualified.
    _run(monkeypatch, json.dumps(_open_payload(
        symbol="DIR", realized_pnl="500", swap_accrued="0", commission="0")),
        ["--journal-path", str(journal), "write"])
    rc, out, _ = _run(monkeypatch, None,
                      ["--journal-path", str(journal), "query", "--swing-only"])
    parsed = json.loads(out)
    assert parsed["count"] == 1
    assert parsed["entries"][0]["symbol"] == "CARRY"


# --- stats ---------------------------------------------------------------


def test_stats_returns_summary(monkeypatch, tmp_path: Path):
    journal = tmp_path / "j.jsonl"
    _run(monkeypatch, json.dumps(_open_payload()),
         ["--journal-path", str(journal), "write"])
    rc, out, err = _run(monkeypatch, None,
                        ["--journal-path", str(journal), "stats"])
    assert rc == 0, err
    parsed = json.loads(out)
    assert parsed["count"] == 1
    assert parsed["summary"]["total_pnl"] == "635.50"


def test_stats_group_by_setup_type(monkeypatch, tmp_path: Path):
    journal = tmp_path / "j.jsonl"
    _run(monkeypatch, json.dumps(_open_payload(setup_type="pullback-long")),
         ["--journal-path", str(journal), "write"])
    _run(monkeypatch, json.dumps(_open_payload(setup_type="breakout-long")),
         ["--journal-path", str(journal), "write"])
    rc, out, _ = _run(monkeypatch, None,
                      ["--journal-path", str(journal), "stats", "--group-by", "setup_type"])
    parsed = json.loads(out)
    assert "by_setup_type" in parsed
    assert set(parsed["by_setup_type"].keys()) == {"pullback-long", "breakout-long"}


def test_stats_group_by_all_includes_every_breakdown(monkeypatch, tmp_path: Path):
    journal = tmp_path / "j.jsonl"
    _run(monkeypatch, json.dumps(_open_payload()),
         ["--journal-path", str(journal), "write"])
    rc, out, _ = _run(monkeypatch, None,
                      ["--journal-path", str(journal), "stats", "--group-by", "all"])
    parsed = json.loads(out)
    assert all(k in parsed for k in (
        "by_setup_type", "by_symbol", "by_side", "by_risk_classification"
    ))


# --- tags ----------------------------------------------------------------


def test_tags_returns_frequency_sorted_list(monkeypatch, tmp_path: Path):
    journal = tmp_path / "j.jsonl"
    for tag in ["pullback-long", "pullback-long", "swap-harvest-long"]:
        _run(monkeypatch, json.dumps(_open_payload(setup_type=tag)),
             ["--journal-path", str(journal), "write"])
    rc, out, _ = _run(monkeypatch, None, ["--journal-path", str(journal), "tags"])
    parsed = json.loads(out)
    assert parsed["tags"][0]["setup_type"] == "pullback-long"
    assert parsed["tags"][0]["count"] == 2


# --- decision write / write-outcome --------------------------------------


def _run_decision_write(path: Path, payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "trading_agent_skills.cli.journal",
         "decision", "write", "--decisions-path", str(path)],
        input=json.dumps(payload), text=True, capture_output=True,
    )


def test_cli_decision_write_intent(tmp_path: Path) -> None:
    decisions = tmp_path / "decisions.jsonl"
    payload = {
        "kind": "open",
        "symbol": "XAUUSD.z",
        "ticket": None,
        "setup_type": "price_action:pin_bar",
        "reasoning": "FVG fill at 2380.",
        "skills_used": ["price-action", "pre-trade-checklist"],
        "guardian_status": "CLEAR",
        "checklist_verdict": "PASS",
        "execution": {
            "side": "BUY", "volume": "0.05", "entry_price": "2380.00",
            "sl": "2375.00", "tp": "2390.00",
        },
        "charter_version": 1,
        "tick_id": "2026-04-30T22:00:00Z",
    }
    res = _run_decision_write(decisions, payload)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["status"] == "ok"
    assert decisions.is_file()
    rec = json.loads(decisions.read_text().splitlines()[0])
    assert rec["kind"] == "open"


def test_cli_decision_write_outcome(tmp_path: Path) -> None:
    decisions = tmp_path / "decisions.jsonl"
    # Seed an intent
    _run_decision_write(decisions, {
        "kind": "open", "symbol": "X", "ticket": None,
        "setup_type": "x", "reasoning": "r", "skills_used": [],
        "guardian_status": "CLEAR", "checklist_verdict": "PASS",
        "execution": {"side": "BUY", "volume": "0.1", "entry_price": "1.0",
                      "sl": "0.99", "tp": "1.02"},
        "charter_version": 1, "tick_id": "2026-04-30T22:00:00Z",
    })
    # Outcome
    res = subprocess.run(
        [sys.executable, "-m", "trading_agent_skills.cli.journal",
         "decision", "write-outcome", "--decisions-path", str(decisions)],
        input=json.dumps({
            "tick_id": "2026-04-30T22:00:00Z",
            "kind": "open", "symbol": "X",
            "execution_status": "filled", "ticket": 1234,
            "actual_fill_price": "1.0001", "failure_reason": None,
        }),
        text=True, capture_output=True,
    )
    assert res.returncode == 0, res.stderr
    lines = decisions.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["execution"]["execution_status"] == "filled"


def test_cli_decision_write_invalid_payload_nonzero_exit(tmp_path: Path) -> None:
    decisions = tmp_path / "decisions.jsonl"
    res = _run_decision_write(decisions, {"kind": "explode"})  # missing fields too
    assert res.returncode != 0
    assert "kind" in res.stderr or "kind" in res.stdout


# --- decision read ------------------------------------------------------


def test_cli_decision_read_filters(tmp_path: Path) -> None:
    decisions = tmp_path / "decisions.jsonl"
    # Seed two intents
    for sym in ("XAUUSD.z", "EURUSD.z"):
        _run_decision_write(decisions, {
            "kind": "open", "symbol": sym, "ticket": None,
            "setup_type": "price_action:pin_bar", "reasoning": "r",
            "skills_used": [], "guardian_status": "CLEAR", "checklist_verdict": "PASS",
            "execution": {"side": "BUY", "volume": "0.1", "entry_price": "1.0",
                          "sl": "0.99", "tp": "1.02"},
            "charter_version": 1, "tick_id": "2026-04-30T22:00:00Z",
        })
    res = subprocess.run(
        [sys.executable, "-m", "trading_agent_skills.cli.journal",
         "decision", "read", "--decisions-path", str(decisions),
         "--symbol", "XAUUSD.z"],
        text=True, capture_output=True,
    )
    assert res.returncode == 0
    out = json.loads(res.stdout)
    assert len(out["records"]) == 1
    assert out["records"][0]["symbol"] == "XAUUSD.z"


def test_cli_journal_account_id_routes_writes(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    payload = {
        "symbol": "XAUUSD.z", "side": "buy", "volume": "0.1",
        "entry_price": "2380.00", "exit_price": "2390.00",
        "entry_time": "2026-04-30T08:00:00+00:00",
        "exit_time": "2026-04-30T16:00:00+00:00",
        "original_stop_distance_points": 50,
        "original_risk_amount": "100.00", "realized_pnl": "100.00",
        "swap_accrued": "0.00", "commission": "0.00",
        "setup_type": "price_action:pin_bar", "rationale": "test",
        "risk_classification_at_close": "AT_RISK",
    }
    env = {**os.environ, "HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}
    res = subprocess.run(
        [sys.executable, "-m", "trading_agent_skills.cli.journal",
         "--account-id", "12345678", "write"],
        input=json.dumps(payload), text=True, capture_output=True, env=env,
    )
    assert res.returncode == 0, res.stderr
    expected = tmp_path / ".trading-agent-skills" / "accounts" / "12345678" / "journal.jsonl"
    assert expected.is_file()


def test_cli_decision_read_since(tmp_path: Path) -> None:
    decisions = tmp_path / "decisions.jsonl"
    for tick_day, sym in (("2026-04-25T00:00:00Z", "OLD"), ("2026-04-30T00:00:00Z", "NEW")):
        _run_decision_write(decisions, {
            "kind": "open", "symbol": sym, "ticket": None,
            "setup_type": "x", "reasoning": "r", "skills_used": [],
            "guardian_status": "CLEAR", "checklist_verdict": "PASS",
            "execution": {"side": "BUY", "volume": "0.1", "entry_price": "1.0",
                          "sl": "0.99", "tp": "1.02"},
            "charter_version": 1, "tick_id": tick_day,
        })
    res = subprocess.run(
        [sys.executable, "-m", "trading_agent_skills.cli.journal",
         "decision", "read", "--decisions-path", str(decisions),
         "--since", "2026-04-29T00:00:00Z"],
        text=True, capture_output=True,
    )
    assert res.returncode == 0
    out = json.loads(res.stdout)
    assert {r["symbol"] for r in out["records"]} == {"NEW"}


# --- migrate-to-sqlite ---------------------------------------------------


def _open_kwargs(**overrides) -> dict:
    """Canonical open-record kwargs used across migrate tests."""
    base = dict(
        symbol="UKOIL",
        side="buy",
        volume="1.0",
        entry_price="75.42",
        exit_price="78.10",
        entry_time="2026-04-29T07:30:00+00:00",
        exit_time="2026-05-02T15:45:00+00:00",
        original_stop_distance_points=80,
        original_risk_amount="80.00",
        realized_pnl="268.00",
        swap_accrued="375.00",
        commission="-7.50",
        setup_type="swap-harvest-long",
        rationale="Geopolitical tension intact; oversold on D1; positive carry.",
        risk_classification_at_close="LOCKED_PROFIT",
        ticket=12345,
    )
    base.update(overrides)
    return base


def test_migrate_to_sqlite_imports_all_record_types(tmp_path) -> None:
    """Hand-roll a JSONL with one of each type and verify all five tables fill."""
    import json
    import sqlite3
    from trading_agent_skills.journal_io import _sibling_db_path
    from trading_agent_skills.cli.journal import main as journal_cli

    p = tmp_path / "journal.jsonl"
    records = [
        {
            "schema_version": 1, "uuid": "u1", "type": "open", "ticket": None,
            "symbol": "EURUSD", "side": "buy", "volume": "1.0",
            "entry_price": "1.0800", "exit_price": "1.0850",
            "entry_time": "2026-04-29T07:30:00+00:00",
            "exit_time": "2026-04-29T14:15:00+00:00",
            "original_stop_distance_points": 30, "original_risk_amount": "50.00",
            "realized_pnl": "50.00", "swap_accrued": "0", "commission": "-1.00",
            "setup_type": "test", "rationale": "test",
            "risk_classification_at_close": "RISK_FREE",
            "outcome_notes": None, "_written_at": "2026-04-29T14:16:00+00:00",
        },
        {
            "schema_version": 1, "uuid": "u1", "type": "update",
            "update_time": "2026-04-30T10:00:00+00:00",
            "rationale": "next-day reflection",
        },
        {
            "schema_version": 1, "uuid": "u1", "type": "sl-trailed",
            "ts": "2026-04-29T09:00:00+00:00",
            "old_sl": "1.0780", "new_sl": "1.0805",
            "old_tp": "1.0850", "new_tp": "1.0850",
            "reason": "breakeven", "paper_mode": False,
        },
        {
            "schema_version": 1, "uuid": "u1", "type": "partial-closed",
            "ts": "2026-04-29T11:00:00+00:00",
            "closed_lots": "0.50", "remaining_lots": "0.50",
            "realized_pnl": "25.00", "reason": "tp1", "paper_mode": False,
        },
        {
            "schema_version": 1, "uuid": "u1", "type": "closed",
            "ts": "2026-04-29T14:15:00+00:00",
            "exit_price": "1.0850", "realized_pnl": "50.00",
            "close_kind": "manual", "reason": "session-end", "paper_mode": False,
        },
    ]
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    rc = journal_cli(["migrate-to-sqlite", "--journal-path", str(p)])
    assert rc == 0

    db = _sibling_db_path(p)
    con = sqlite3.connect(db)
    counts = {
        t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("journal_open", "journal_updates", "journal_sl_trailed",
                  "journal_partial_closed", "journal_closed")
    }
    assert counts == {
        "journal_open": 1,
        "journal_updates": 1,
        "journal_sl_trailed": 1,
        "journal_partial_closed": 1,
        "journal_closed": 1,
    }
    con.close()


def test_migrate_to_sqlite_is_idempotent(tmp_path) -> None:
    import sqlite3
    from trading_agent_skills.journal_io import (
        _sibling_db_path, write_open, write_sl_trailed,
    )
    from trading_agent_skills.cli.journal import main as journal_cli

    p = tmp_path / "journal.jsonl"
    uid = write_open(p, **_open_kwargs())  # dual-writes already
    write_sl_trailed(
        p, uuid=uid, old_sl="74.50", new_sl="75.20",
        reason="breakeven", paper_mode=False,
    )

    # Re-migrate twice: no-op (uuid already in DB, UNIQUE constraint dedupes events).
    journal_cli(["migrate-to-sqlite", "--journal-path", str(p)])
    journal_cli(["migrate-to-sqlite", "--journal-path", str(p)])

    con = sqlite3.connect(_sibling_db_path(p))
    (n_open,) = con.execute("SELECT COUNT(*) FROM journal_open WHERE uuid=?", (uid,)).fetchone()
    (n_trail,) = con.execute("SELECT COUNT(*) FROM journal_sl_trailed WHERE uuid=?", (uid,)).fetchone()
    assert n_open == 1
    assert n_trail == 1
    con.close()


def test_export_jsonl_round_trips_all_record_types(tmp_path) -> None:
    from trading_agent_skills.journal_io import (
        write_open, write_update, write_sl_trailed,
        write_partial_closed, write_close, read_raw,
    )
    from trading_agent_skills.cli.journal import main as journal_cli

    src = tmp_path / "journal.jsonl"
    uid = write_open(src, **_open_kwargs())
    write_update(src, uuid=uid, rationale="Reflected later.")
    write_sl_trailed(
        src, uuid=uid, old_sl="74.50", new_sl="75.20",
        reason="breakeven", paper_mode=False,
    )
    write_partial_closed(
        src, uuid=uid, closed_lots="0.50", remaining_lots="0.50",
        realized_pnl="120.00", reason="tp1", paper_mode=False,
    )
    write_close(
        src, uuid=uid, exit_price="77.90", realized_pnl="248.00",
        close_kind="manual", reason="session-end", paper_mode=False,
    )

    out = tmp_path / "exported.jsonl"
    rc = journal_cli([
        "export-jsonl",
        "--journal-path", str(src),
        "--out", str(out),
    ])
    assert rc == 0

    exported = read_raw(out)
    assert len(exported) == 5
    assert sorted(r["type"] for r in exported) == [
        "closed", "open", "partial-closed", "sl-trailed", "update",
    ]
