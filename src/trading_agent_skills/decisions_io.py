"""Decisions dual-write layer (Phase B of the Option β SQLite migration).

Mirrors journal_io.py's shape:
  - public `append(path, record)` — JSONL-first, then SQLite (raises on failure).
  - public `read_raw(path)` — SQLite-first with JSONL fallback (idempotent on
    missing/uninitialized DB).

Records are heterogeneous (three different writers historically — decision_log.py,
Stage 1/2/3 LLM prompts, and earlier prompt iterations). The schema promotes a
stable set of columns and keeps the full original record in a `payload` JSON
column so export round-trips perfectly.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterator, Optional


SCHEMA_VERSION = 1  # for records emitted by decisions_io's own writers (none today; reserved).


class DecisionSchemaError(ValueError):
    """A decision record violates the (very minimal) required schema."""


_ACCOUNT_PATH_RE = re.compile(r"/accounts/(?P<id>[^/]+)/decisions\.jsonl$")


def _normalize(record: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy with `ts` and `record_type` filled in.

    Required: one of `ts` or `timestamp` must be present. Raises
    DecisionSchemaError otherwise. Preserves the original record's keys
    verbatim — the only additions are derived columns (`ts` if it came in
    as `timestamp`, plus `record_type`). The original `timestamp` field is
    kept so payload export round-trips exactly.
    """
    if not isinstance(record, dict):
        raise DecisionSchemaError(f"record must be a dict, got {type(record).__name__}")

    out = dict(record)

    ts = out.get("ts") or out.get("timestamp")
    if not ts or not isinstance(ts, str):
        raise DecisionSchemaError(
            "decision record must have a non-empty `ts` or `timestamp` field"
        )
    out["ts"] = ts  # idempotent if `ts` was already set

    out["record_type"] = out.get("kind") or out.get("type")

    return out


def _canonical_payload(record: dict[str, Any]) -> str:
    """Stable JSON projection for content hashing. Order-independent."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _dedup_key(canonical_payload: str) -> str:
    """sha256 hex of the canonical projection. UNIQUE constraint key in SQLite."""
    return hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()


_DECISIONS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    record_type     TEXT,
    fire            TEXT,
    run_id          TEXT,
    symbol          TEXT,
    ticket_id       INTEGER,
    tick_id         TEXT,
    schema_version  INTEGER,
    account         TEXT,
    paper_mode      INTEGER,
    is_outcome      INTEGER,
    payload         TEXT NOT NULL,
    dedup_key       TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_decisions_ts          ON decisions(ts);
CREATE INDEX IF NOT EXISTS idx_decisions_record_type ON decisions(record_type);
CREATE INDEX IF NOT EXISTS idx_decisions_run_id      ON decisions(run_id)      WHERE run_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_decisions_symbol      ON decisions(symbol)      WHERE symbol IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_decisions_ticket_id   ON decisions(ticket_id)   WHERE ticket_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_decisions_tick_id     ON decisions(tick_id)     WHERE tick_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_decisions_fire_ts     ON decisions(fire, ts);
"""


def _init_decisions_table(con: sqlite3.Connection) -> None:
    """Create the `decisions` table + indexes if missing. Idempotent."""
    con.executescript(_DECISIONS_SCHEMA_SQL)
    con.commit()


def _connect_and_init(db_path: Path) -> sqlite3.Connection:
    """Open trader.db, ensure the decisions table exists, return the connection.

    Does NOT initialise Phase A's journal tables — those are managed by
    journal_io._connect_and_init. The two modules coexist on the same DB.
    CREATE IF NOT EXISTS makes both initializers safe to call in any order.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    _init_decisions_table(con)
    return con
