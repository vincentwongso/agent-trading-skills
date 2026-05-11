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
