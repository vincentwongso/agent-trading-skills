"""Phase B: decisions_io dual-write to SQLite + read-side cutover."""

import json
import sqlite3
from pathlib import Path

import pytest

from trading_agent_skills.decisions_io import (
    DecisionSchemaError,
    _canonical_payload,
    _connect_and_init,
    _dedup_key,
    _init_decisions_table,
    _normalize,
    _sibling_db_path,
    _sqlite_write,
    append as decisions_append,
)


def test_normalize_keeps_ts_when_present() -> None:
    rec = {"ts": "2026-05-11T00:00:00Z", "type": "stage1"}
    out = _normalize(rec)
    assert out["ts"] == "2026-05-11T00:00:00Z"
    assert out["record_type"] == "stage1"


def test_normalize_coalesces_timestamp_into_ts() -> None:
    rec = {"timestamp": "2026-05-11T00:00:00Z", "type": "stage2-complete"}
    out = _normalize(rec)
    assert out["ts"] == "2026-05-11T00:00:00Z"
    # original `timestamp` is preserved (round-trippable via payload).
    assert out["timestamp"] == "2026-05-11T00:00:00Z"
    assert out["record_type"] == "stage2-complete"


def test_normalize_kind_wins_over_type_for_record_type() -> None:
    # decision_log.py rows have `kind`; if a record somehow has both, kind wins
    # because that's the canonical writer.
    rec = {"ts": "2026-05-11T00:00:00Z", "kind": "open", "type": "stage2-complete"}
    out = _normalize(rec)
    assert out["record_type"] == "open"


def test_normalize_falls_back_to_type_when_no_kind() -> None:
    rec = {"ts": "2026-05-11T00:00:00Z", "type": "stage1"}
    assert _normalize(rec)["record_type"] == "stage1"


def test_normalize_record_type_is_none_when_neither_present() -> None:
    rec = {"ts": "2026-05-11T00:00:00Z", "fire": "stage1", "decision": "no-trigger"}
    assert _normalize(rec)["record_type"] is None


def test_normalize_raises_when_no_timestamp() -> None:
    rec = {"type": "stage1", "decision": "no-trigger"}
    with pytest.raises(DecisionSchemaError, match="timestamp"):
        _normalize(rec)


def test_normalize_does_not_mutate_input() -> None:
    rec = {"timestamp": "2026-05-11T00:00:00Z", "type": "stage1"}
    snapshot = json.dumps(rec, sort_keys=True)
    _normalize(rec)
    assert json.dumps(rec, sort_keys=True) == snapshot


def test_canonical_payload_is_stable_across_key_orders() -> None:
    a = {"ts": "2026-05-11T00:00:00Z", "type": "stage1", "fire": "stage1"}
    b = {"fire": "stage1", "type": "stage1", "ts": "2026-05-11T00:00:00Z"}
    assert _canonical_payload(a) == _canonical_payload(b)


def test_dedup_key_is_64char_hex() -> None:
    payload = _canonical_payload({"ts": "2026-05-11T00:00:00Z", "type": "stage1"})
    key = _dedup_key(payload)
    assert len(key) == 64
    assert all(c in "0123456789abcdef" for c in key)


def test_dedup_key_differs_for_different_records() -> None:
    a = _dedup_key(_canonical_payload({"ts": "2026-05-11T00:00:00Z", "type": "stage1"}))
    b = _dedup_key(_canonical_payload({"ts": "2026-05-11T00:00:00Z", "type": "stage2-complete"}))
    assert a != b


def test_init_decisions_table_creates_table(tmp_path: Path) -> None:
    db_path = tmp_path / "trader.db"
    con = sqlite3.connect(db_path)
    _init_decisions_table(con)
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='decisions'"
    ).fetchall()
    assert rows == [("decisions",)]
    con.close()


def test_init_decisions_table_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "trader.db"
    con = sqlite3.connect(db_path)
    _init_decisions_table(con)
    _init_decisions_table(con)  # second call must not raise
    con.close()


def test_init_decisions_table_creates_expected_indexes(tmp_path: Path) -> None:
    db_path = tmp_path / "trader.db"
    con = sqlite3.connect(db_path)
    _init_decisions_table(con)
    idx_names = {
        row[0]
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='decisions'"
        )
    }
    # Auto-index from UNIQUE is sqlite_autoindex_decisions_1; check the ones we
    # created explicitly.
    assert "idx_decisions_ts" in idx_names
    assert "idx_decisions_record_type" in idx_names
    assert "idx_decisions_run_id" in idx_names
    assert "idx_decisions_symbol" in idx_names
    assert "idx_decisions_ticket_id" in idx_names
    assert "idx_decisions_tick_id" in idx_names
    assert "idx_decisions_fire_ts" in idx_names
    con.close()


def test_connect_and_init_creates_parent_dir(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "trader.db"
    assert not db_path.parent.exists()
    con = _connect_and_init(db_path)
    assert db_path.parent.exists()
    assert db_path.exists()
    con.close()


def test_connect_and_init_coexists_with_journal_tables(tmp_path: Path) -> None:
    """trader.db is shared with Phase A's journal tables. Decisions init must
    not interfere with already-initialized journal tables."""
    from trading_agent_skills.journal_io import _connect_and_init as journal_connect

    db_path = tmp_path / "trader.db"
    jcon = journal_connect(db_path)
    jcon.close()

    # Now layer decisions init on top.
    dcon = _connect_and_init(db_path)
    table_names = {
        row[0] for row in dcon.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    # Phase A tables still present:
    assert "journal_open" in table_names
    assert "journal_updates" in table_names
    assert "journal_sl_trailed" in table_names
    assert "journal_partial_closed" in table_names
    assert "journal_closed" in table_names
    # Phase B table added:
    assert "decisions" in table_names
    dcon.close()


def test_sibling_db_path() -> None:
    p = Path("/tmp/accounts/1234/decisions.jsonl")
    assert _sibling_db_path(p) == Path("/tmp/accounts/1234/trader.db")


def test_sqlite_write_inserts_decision_log_record(tmp_path: Path) -> None:
    decisions_path = tmp_path / "accounts" / "x" / "decisions.jsonl"
    decisions_path.parent.mkdir(parents=True)
    record = _normalize({
        "schema_version": 1,
        "ts": "2026-05-11T00:00:00Z",
        "kind": "skip",
        "symbol": "XAUUSD",
        "tick_id": "2026-05-11T00:00:00Z",
        "reasoning": "test",
        "skills_used": [],
        "guardian_status": "CLEAR",
        "checklist_verdict": None,
        "execution": None,
        "charter_version": 1,
        "ticket": None,
        "setup_type": None,
    })
    _sqlite_write(decisions_path, record)

    db_path = _sibling_db_path(decisions_path)
    con = sqlite3.connect(db_path)
    rows = list(con.execute("SELECT ts, record_type, symbol, tick_id, schema_version, payload FROM decisions"))
    con.close()
    assert len(rows) == 1
    ts, record_type, symbol, tick_id, schema_version, payload = rows[0]
    assert ts == "2026-05-11T00:00:00Z"
    assert record_type == "skip"
    assert symbol == "XAUUSD"
    assert tick_id == "2026-05-11T00:00:00Z"
    assert schema_version == 1
    parsed = json.loads(payload)
    assert parsed["kind"] == "skip"
    assert parsed["symbol"] == "XAUUSD"


def test_sqlite_write_inserts_prompt_stage1_record(tmp_path: Path) -> None:
    decisions_path = tmp_path / "accounts" / "x" / "decisions.jsonl"
    decisions_path.parent.mkdir(parents=True)
    record = _normalize({
        "ts": "2026-05-11T01:00:00Z",
        "type": "stage1-terminal",
        "fire": "stage1",
        "symbol": "XAUUSD",
        "run_id": "abc123",
        "decision": "triggered",
        "paper_mode": True,
        "account": "7000522",
    })
    _sqlite_write(decisions_path, record)
    db_path = _sibling_db_path(decisions_path)
    con = sqlite3.connect(db_path)
    row = con.execute(
        "SELECT ts, record_type, fire, run_id, symbol, account, paper_mode FROM decisions"
    ).fetchone()
    con.close()
    assert row == ("2026-05-11T01:00:00Z", "stage1-terminal", "stage1", "abc123", "XAUUSD", "7000522", 1)


def test_sqlite_write_extracts_account_from_path_when_absent(tmp_path: Path) -> None:
    decisions_path = tmp_path / "accounts" / "7000522" / "decisions.jsonl"
    decisions_path.parent.mkdir(parents=True)
    record = _normalize({
        "ts": "2026-05-11T01:00:00Z",
        "type": "stage1-terminal",
        "decision": "no-trigger",
    })
    _sqlite_write(decisions_path, record)
    db_path = _sibling_db_path(decisions_path)
    con = sqlite3.connect(db_path)
    account = con.execute("SELECT account FROM decisions").fetchone()[0]
    con.close()
    assert account == "7000522"


def test_sqlite_write_is_idempotent_on_dedup_key(tmp_path: Path) -> None:
    decisions_path = tmp_path / "accounts" / "x" / "decisions.jsonl"
    decisions_path.parent.mkdir(parents=True)
    record = _normalize({"ts": "2026-05-11T00:00:00Z", "type": "stage1"})
    _sqlite_write(decisions_path, record)
    _sqlite_write(decisions_path, record)  # second write must be a no-op
    db_path = _sibling_db_path(decisions_path)
    con = sqlite3.connect(db_path)
    count = con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    con.close()
    assert count == 1


def test_append_writes_jsonl_then_sqlite(tmp_path: Path) -> None:
    decisions_path = tmp_path / "accounts" / "7000522" / "decisions.jsonl"
    record = {
        "ts": "2026-05-11T00:00:00Z",
        "type": "stage1-terminal",
        "fire": "stage1",
        "decision": "triggered",
        "symbol": "XAUUSD",
        "run_id": "abc123",
    }
    out = decisions_append(decisions_path, record)
    lines = decisions_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["type"] == "stage1-terminal"
    db_path = _sibling_db_path(decisions_path)
    con = sqlite3.connect(db_path)
    count = con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    con.close()
    assert count == 1
    assert out["record_type"] == "stage1-terminal"
    assert out["ts"] == "2026-05-11T00:00:00Z"


def test_append_creates_parent_dir(tmp_path: Path) -> None:
    decisions_path = tmp_path / "fresh" / "decisions.jsonl"
    decisions_append(decisions_path, {"ts": "2026-05-11T00:00:00Z", "type": "stage1"})
    assert decisions_path.exists()


def test_append_dual_write_idempotency_in_sqlite(tmp_path: Path) -> None:
    decisions_path = tmp_path / "decisions.jsonl"
    rec = {"ts": "2026-05-11T00:00:00Z", "type": "stage1"}
    decisions_append(decisions_path, rec)
    decisions_append(decisions_path, rec)
    assert len(decisions_path.read_text().splitlines()) == 2
    db_path = _sibling_db_path(decisions_path)
    con = sqlite3.connect(db_path)
    count = con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    con.close()
    assert count == 1


def test_append_raises_on_missing_timestamp(tmp_path: Path) -> None:
    decisions_path = tmp_path / "decisions.jsonl"
    with pytest.raises(DecisionSchemaError, match="timestamp"):
        decisions_append(decisions_path, {"type": "stage1"})
    assert not decisions_path.exists()


def test_append_propagates_sqlite_failure_after_jsonl_write(tmp_path: Path, monkeypatch) -> None:
    """If SQLite raises after JSONL append, the JSONL line is durable and the
    exception propagates. Backfill will pick it up later."""
    import trading_agent_skills.decisions_io as decisions_io_mod

    decisions_path = tmp_path / "decisions.jsonl"
    rec = {"ts": "2026-05-11T00:00:00Z", "type": "stage1"}

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("simulated SQLite failure")

    monkeypatch.setattr(decisions_io_mod, "_sqlite_write", boom)
    with pytest.raises(sqlite3.OperationalError):
        decisions_append(decisions_path, rec)
    assert decisions_path.read_text(encoding="utf-8").strip()
