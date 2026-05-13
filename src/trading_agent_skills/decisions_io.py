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
