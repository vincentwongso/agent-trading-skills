"""journal_io dual-write to SQLite + read-side cutover.

Covers all five record types: open, update, sl-trailed, partial-closed, closed.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trading_agent_skills.journal_io import (
    _init_journal_tables,
    _sibling_db_path,
    SCHEMA_VERSION,
    read_raw,
    read_resolved,
    read_resolved_with_events,
    write_close,
    write_open,
    write_partial_closed,
    write_sl_trailed,
    write_update,
)


# Mirror the production fixture in tests/test_journal_io.py so the dual-write
# tests use the same canonical payload.
def _open_kwargs(**overrides) -> dict:
    base = dict(
        symbol="UKOIL",
        side="buy",
        volume="1.0",
        entry_price="75.42",
        exit_price="78.10",
        entry_time=datetime(2026, 4, 29, 7, 30, tzinfo=timezone.utc),
        exit_time=datetime(2026, 5, 2, 15, 45, tzinfo=timezone.utc),
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


def test_sibling_db_path_is_trader_db_in_same_dir(tmp_path: Path) -> None:
    p = tmp_path / "accounts" / "42" / "journal.jsonl"
    assert _sibling_db_path(p) == tmp_path / "accounts" / "42" / "trader.db"


def test_init_journal_tables_creates_all_five_tables(tmp_path: Path) -> None:
    db = tmp_path / "trader.db"
    con = sqlite3.connect(db)
    _init_journal_tables(con)
    _init_journal_tables(con)  # idempotent: second call must not raise
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {r[0] for r in rows}
    assert {
        "journal_open",
        "journal_updates",
        "journal_sl_trailed",
        "journal_partial_closed",
        "journal_closed",
    }.issubset(names)
    con.close()


def test_write_open_dual_writes_to_sqlite(tmp_path: Path) -> None:
    p = tmp_path / "journal.jsonl"
    uid = write_open(p, **_open_kwargs())

    # JSONL still has the record.
    raw_lines = p.read_text().strip().splitlines()
    assert len(raw_lines) == 1
    rec = json.loads(raw_lines[0])
    assert rec["uuid"] == uid

    # SQLite has the record.
    db = _sibling_db_path(p)
    assert db.exists()
    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT uuid, symbol, side, volume, ticket FROM journal_open"
    ).fetchall()
    assert rows == [(uid, "UKOIL", "buy", "1.0", 12345)]
    con.close()


def test_write_open_persists_optional_stage2_fields(tmp_path: Path) -> None:
    p = tmp_path / "journal.jsonl"
    uid = write_open(
        p,
        **_open_kwargs(),
        sl="74.50",
        tp="78.10",
        run_id="abc123",
        paper_mode=True,
    )

    con = sqlite3.connect(_sibling_db_path(p))
    row = con.execute(
        "SELECT sl, tp, run_id, paper_mode FROM journal_open WHERE uuid=?",
        (uid,),
    ).fetchone()
    # _decimal_str preserves the trailing zero because "74.50" is a string.
    assert row == ("74.50", "78.10", "abc123", 1)
    con.close()


def test_write_open_omits_optional_fields_as_null(tmp_path: Path) -> None:
    p = tmp_path / "journal.jsonl"
    uid = write_open(p, **_open_kwargs())  # no sl/tp/run_id/paper_mode

    con = sqlite3.connect(_sibling_db_path(p))
    row = con.execute(
        "SELECT sl, tp, run_id, paper_mode FROM journal_open WHERE uuid=?",
        (uid,),
    ).fetchone()
    assert row == (None, None, None, None)
    con.close()


def test_write_open_duplicate_uuid_replaces(tmp_path: Path) -> None:
    p = tmp_path / "journal.jsonl"
    fixed = "fixed-uuid-1"
    write_open(p, **_open_kwargs(), uuid=fixed)
    # Second call with same uuid: JSONL appends (intentional, that's the existing
    # contract — see existing test_write_open_returns_uuid_and_appends), SQLite
    # uses INSERT OR REPLACE so it doesn't raise.
    write_open(p, **_open_kwargs(), uuid=fixed)

    con = sqlite3.connect(_sibling_db_path(p))
    (n,) = con.execute("SELECT COUNT(*) FROM journal_open").fetchone()
    assert n == 1
    con.close()
