"""End-to-end CLI test for trading-agent-skills-journal subcommands."""

import io
import json
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
