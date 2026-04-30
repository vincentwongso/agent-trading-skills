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
from typing import Any, Iterable, List, Optional


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


def write_outcome(
    path: Path,
    *,
    tick_id: str,
    kind: str,
    symbol: str,
    execution_status: str,
    ticket: Optional[int],
    actual_fill_price: Optional[str],
    failure_reason: Optional[str],
) -> dict[str, Any]:
    if execution_status not in ALLOWED_EXEC_STATUSES or execution_status == "pending":
        raise DecisionSchemaError(
            f"execution_status must be in {set(ALLOWED_EXEC_STATUSES) - {'pending'}}, "
            f"got {execution_status!r}"
        )
    if kind not in ALLOWED_KINDS:
        raise DecisionSchemaError(f"kind must be in {ALLOWED_KINDS}, got {kind!r}")
    _validate_tick_id(tick_id)
    if actual_fill_price is not None and not isinstance(actual_fill_price, str):
        raise DecisionSchemaError("actual_fill_price must be a string or None")

    execution: dict[str, Any] = {"execution_status": execution_status}
    if actual_fill_price is not None:
        execution["actual_fill_price"] = actual_fill_price
    if failure_reason is not None:
        execution["failure_reason"] = failure_reason

    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "symbol": symbol,
        "ticket": ticket,
        "execution": execution,
        "tick_id": tick_id,
        "is_outcome": True,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return record


def _read_records(path: Path) -> Iterable[dict[str, Any]]:
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def reconcile_decisions(path: Path) -> Iterable[dict[str, Any]]:
    """Yield one merged record per (tick_id, kind, symbol).

    Intent record is the base; outcome record (latest by ts) overrides
    execution dict and ticket. Orphan intents (no outcome yet) keep
    execution_status='pending'.
    """
    intents: dict[tuple[str, str, str], dict[str, Any]] = {}
    outcomes: dict[tuple[str, str, str], dict[str, Any]] = {}
    for rec in _read_records(path):
        key = (rec.get("tick_id"), rec.get("kind"), rec.get("symbol"))
        if rec.get("is_outcome"):
            existing = outcomes.get(key)
            if existing is None or rec["ts"] > existing["ts"]:
                outcomes[key] = rec
        else:
            intents[key] = rec

    for key, intent in intents.items():
        merged = dict(intent)
        outcome = outcomes.get(key)
        if outcome:
            merged_exec = dict(intent.get("execution") or {})
            merged_exec.update(outcome["execution"])
            merged["execution"] = merged_exec
            if outcome.get("ticket") is not None:
                merged["ticket"] = outcome["ticket"]
        yield merged
