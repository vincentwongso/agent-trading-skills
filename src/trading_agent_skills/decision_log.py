"""Append-only decisions.jsonl — every executed action OR evaluated-but-skipped candidate.

Schema is version-tagged. Records are written in two phases:
  1. Intent record with execution.execution_status = "pending" BEFORE broker call
  2. Outcome record with same (tick_id, kind, symbol) updating execution_status
     to filled / rejected / broker_error AFTER broker call

Reader joins on (tick_id, kind, symbol), latest-by-ts wins for execution state;
reasoning from the intent record is canonical.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional


SCHEMA_VERSION = 1
ALLOWED_KINDS = ("open", "modify", "close", "skip", "mode_change")
ALLOWED_EXEC_STATUSES = ("pending", "filled", "rejected", "broker_error")
ALLOWED_GUARDIAN = ("CLEAR", "CAUTION", "HALT")
ALLOWED_CHECKLIST = ("PASS", "WARN", "BLOCK", None)
ALLOWED_SIDES = ("BUY", "SELL")

_DECIMAL_EXEC_FIELDS = ("volume", "entry_price", "sl", "tp")


class DecisionSchemaError(ValueError):
    """A decision record violates the required schema."""


def _validate_tick_id(tick_id: str) -> None:
    if not isinstance(tick_id, str):
        raise DecisionSchemaError(f"tick_id must be a string, got {type(tick_id).__name__}")
    try:
        dt = datetime.fromisoformat(tick_id.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DecisionSchemaError(f"tick_id: invalid ISO 8601 — {exc}") from exc
    if dt.tzinfo is None:
        raise DecisionSchemaError("tick_id: must be timezone-aware (Z or offset suffix)")


def _validate_execution_block(execution: dict[str, Any]) -> None:
    if not isinstance(execution, dict):
        raise DecisionSchemaError("execution must be a dict")
    if execution.get("side") not in ALLOWED_SIDES:
        raise DecisionSchemaError(
            f"execution.side must be in {ALLOWED_SIDES}, got {execution.get('side')!r}"
        )
    for field in _DECIMAL_EXEC_FIELDS:
        v = execution.get(field)
        if not isinstance(v, str) or not v:
            raise DecisionSchemaError(
                f"execution.{field} must be a non-empty string (Decimal-as-string), got {v!r}"
            )


def write_intent(
    path: Path,
    *,
    kind: str,
    symbol: str,
    ticket: Optional[int],
    setup_type: str,
    reasoning: str,
    skills_used: List[str],
    guardian_status: str,
    checklist_verdict: Optional[str],
    execution: Optional[dict[str, Any]],
    charter_version: int,
    tick_id: str,
) -> dict[str, Any]:
    if kind not in ALLOWED_KINDS:
        raise DecisionSchemaError(f"kind must be in {ALLOWED_KINDS}, got {kind!r}")
    if guardian_status not in ALLOWED_GUARDIAN:
        raise DecisionSchemaError(
            f"guardian_status must be in {ALLOWED_GUARDIAN}, got {guardian_status!r}"
        )
    if checklist_verdict not in ALLOWED_CHECKLIST:
        raise DecisionSchemaError(
            f"checklist_verdict must be in {ALLOWED_CHECKLIST}, got {checklist_verdict!r}"
        )
    if not symbol:
        raise DecisionSchemaError("symbol is required")
    if not reasoning:
        raise DecisionSchemaError("reasoning is required")
    if kind in ("open", "skip") and not setup_type:
        raise DecisionSchemaError(f"setup_type is required for kind={kind!r}")
    if kind in ("open", "modify", "close") and execution is None:
        raise DecisionSchemaError(f"execution is required for kind={kind!r}")
    if execution is not None:
        _validate_execution_block(execution)
        execution = {**execution, "execution_status": "pending"}
    _validate_tick_id(tick_id)

    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "symbol": symbol,
        "ticket": ticket,
        "setup_type": setup_type or None,
        "reasoning": reasoning,
        "skills_used": list(skills_used),
        "guardian_status": guardian_status,
        "checklist_verdict": checklist_verdict,
        "execution": execution,
        "charter_version": charter_version,
        "tick_id": tick_id,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return record
