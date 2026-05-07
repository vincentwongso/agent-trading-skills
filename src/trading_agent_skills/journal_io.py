"""Append-only JSONL trade journal — write, read, and resolve update chains.

Schema (``schema_version: 1``):

  - ``open`` entry: canonical record at trade close. Required fields cover
    enough that R-multiple, win rate, expectancy, and swap-only P&L can be
    computed without re-querying MT5.
  - ``update`` entry: post-trade reflection or correction. References an
    earlier ``uuid`` and patches a subset of fields. Read-side resolves the
    latest state per uuid.

Why JSONL: human-readable, trivially backed up, append-safe under crash
(an interrupted write loses at most one trailing line, never corrupts the
rest). Why not SQLite: complete overkill for one-user / one-machine
journaling, and JSONL inspects fine in any text editor.

All money / price / volume fields are stored as fixed-point strings (the
mt5-mcp convention) so float drift never enters the journal. Timestamps
are aware UTC ISO 8601 (e.g. ``2026-04-29T07:30:00+00:00``).
"""

from __future__ import annotations

import json
import os
import uuid as uuid_mod
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Literal, Optional

from trading_agent_skills.decimal_io import D


SCHEMA_VERSION = 1
ALLOWED_RISK_CLASSIFICATIONS = ("AT_RISK", "RISK_FREE", "LOCKED_PROFIT")
ALLOWED_SIDES = ("buy", "sell")
ALLOWED_CLOSE_KINDS = ("invalidation", "manual")

# Fields stored as Decimal (serialised as fixed-point strings).
_DECIMAL_FIELDS = {
    "volume",
    "entry_price",
    "exit_price",
    "original_risk_amount",
    "realized_pnl",
    "swap_accrued",
    "commission",
}
_DATETIME_FIELDS = {"entry_time", "exit_time", "update_time"}


class SchemaError(ValueError):
    """An entry violates the journal schema (missing fields, wrong types, etc.)."""


# --- helpers ---------------------------------------------------------------


def _ensure_aware_utc(dt: Any, field_name: str) -> str:
    """Coerce a datetime/string to ISO 8601 UTC; reject naive."""
    if isinstance(dt, str):
        try:
            parsed = datetime.fromisoformat(dt)
        except ValueError as exc:
            raise SchemaError(f"{field_name}: invalid ISO 8601 — {exc}") from exc
        dt = parsed
    if not isinstance(dt, datetime):
        raise SchemaError(f"{field_name}: expected datetime or ISO 8601 string")
    if dt.tzinfo is None:
        raise SchemaError(f"{field_name}: must be timezone-aware UTC")
    if dt.utcoffset() != timezone.utc.utcoffset(dt):
        # Convert non-UTC aware to UTC; don't reject (fewer surprises for
        # callers using zoneinfo).
        dt = dt.astimezone(timezone.utc)
    # Use isoformat with +00:00 (not Z) for round-trip compatibility with
    # datetime.fromisoformat in Python <3.11.
    return dt.isoformat()


def _decimal_str(value: Any, field_name: str) -> str:
    """Coerce to Decimal via decimal_io.D, then format as fixed-point string."""
    try:
        d = D(value)
    except (TypeError, ValueError) as exc:
        raise SchemaError(f"{field_name}: {exc}") from exc
    return format(d, "f")


def _new_uuid() -> str:
    return str(uuid_mod.uuid4())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- write -----------------------------------------------------------------


def write_open(
    path: Path | str,
    *,
    symbol: str,
    side: str,
    volume: Decimal | str,
    entry_price: Decimal | str,
    exit_price: Decimal | str,
    entry_time: datetime | str,
    exit_time: datetime | str,
    original_stop_distance_points: int,
    original_risk_amount: Decimal | str,
    realized_pnl: Decimal | str,
    swap_accrued: Decimal | str,
    commission: Decimal | str,
    setup_type: str,
    rationale: str,
    risk_classification_at_close: str,
    ticket: Optional[int] = None,
    outcome_notes: Optional[str] = None,
    uuid: Optional[str] = None,
) -> str:
    """Append a new ``open`` entry. Returns the generated (or supplied) uuid.

    Validation is strict at the write boundary — the journal is the source
    of truth for performance stats, and silently accepting bad data here
    poisons every retrospective query downstream.
    """
    if side not in ALLOWED_SIDES:
        raise SchemaError(f"side must be one of {ALLOWED_SIDES}, got {side!r}")
    if risk_classification_at_close not in ALLOWED_RISK_CLASSIFICATIONS:
        raise SchemaError(
            f"risk_classification_at_close must be one of {ALLOWED_RISK_CLASSIFICATIONS}, "
            f"got {risk_classification_at_close!r}"
        )
    if not symbol:
        raise SchemaError("symbol is required")
    if not setup_type:
        raise SchemaError("setup_type is required")
    if not rationale:
        raise SchemaError("rationale is required")
    if not isinstance(original_stop_distance_points, int):
        raise SchemaError("original_stop_distance_points must be int")
    if original_stop_distance_points <= 0:
        raise SchemaError("original_stop_distance_points must be > 0 for R-multiple math")

    record_uuid = uuid or _new_uuid()
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "uuid": record_uuid,
        "type": "open",
        "ticket": ticket,
        "symbol": symbol,
        "side": side,
        "volume": _decimal_str(volume, "volume"),
        "entry_price": _decimal_str(entry_price, "entry_price"),
        "exit_price": _decimal_str(exit_price, "exit_price"),
        "entry_time": _ensure_aware_utc(entry_time, "entry_time"),
        "exit_time": _ensure_aware_utc(exit_time, "exit_time"),
        "original_stop_distance_points": original_stop_distance_points,
        "original_risk_amount": _decimal_str(original_risk_amount, "original_risk_amount"),
        "realized_pnl": _decimal_str(realized_pnl, "realized_pnl"),
        "swap_accrued": _decimal_str(swap_accrued, "swap_accrued"),
        "commission": _decimal_str(commission, "commission"),
        "setup_type": setup_type,
        "rationale": rationale,
        "risk_classification_at_close": risk_classification_at_close,
        "outcome_notes": outcome_notes,
        "_written_at": _now_iso(),
    }
    _append_line(path, record)
    return record_uuid


def write_update(
    path: Path | str,
    *,
    uuid: str,
    setup_type: Optional[str] = None,
    rationale: Optional[str] = None,
    risk_classification_at_close: Optional[str] = None,
    outcome_notes: Optional[str] = None,
) -> None:
    """Append an ``update`` patch referencing an existing ``open`` uuid.

    Only fields explicitly passed are recorded — None means "don't touch".
    The read-side composes patches in chronological order over the original
    open entry.
    """
    patches: dict[str, Any] = {}
    if setup_type is not None:
        if not setup_type:
            raise SchemaError("setup_type, if patched, cannot be empty")
        patches["setup_type"] = setup_type
    if rationale is not None:
        patches["rationale"] = rationale
    if risk_classification_at_close is not None:
        if risk_classification_at_close not in ALLOWED_RISK_CLASSIFICATIONS:
            raise SchemaError(
                f"risk_classification_at_close must be one of {ALLOWED_RISK_CLASSIFICATIONS}"
            )
        patches["risk_classification_at_close"] = risk_classification_at_close
    if outcome_notes is not None:
        patches["outcome_notes"] = outcome_notes
    if not patches:
        raise SchemaError("write_update requires at least one patched field")

    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "uuid": uuid,
        "type": "update",
        "update_time": _now_iso(),
        **patches,
    }
    _append_line(path, record)


def write_sl_trailed(
    path: Path | str,
    *,
    uuid: str,
    old_sl: Decimal | str,
    new_sl: Decimal | str,
    reason: str,
    old_tp: Decimal | str | None = None,
    new_tp: Decimal | str | None = None,
    paper_mode: bool = False,
) -> None:
    """Append an ``sl-trailed`` event referencing an existing ``open`` uuid.

    Stage 3 (position management) records SL adjustments as discrete events
    so trail-history can be reconstructed. Existing ``read_resolved`` ignores
    these events; ``read_resolved_with_events`` attaches them to the parent
    open as ``_events: [...]``.
    """
    if not reason:
        raise SchemaError("reason is required for sl-trailed event")
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "uuid": uuid,
        "type": "sl-trailed",
        "ts": _now_iso(),
        "old_sl": _decimal_str(old_sl, "old_sl"),
        "new_sl": _decimal_str(new_sl, "new_sl"),
        "old_tp": _decimal_str(old_tp, "old_tp") if old_tp is not None else None,
        "new_tp": _decimal_str(new_tp, "new_tp") if new_tp is not None else None,
        "reason": reason,
        "paper_mode": bool(paper_mode),
    }
    _append_line(path, record)


def write_partial_closed(
    path: Path | str,
    *,
    uuid: str,
    closed_lots: Decimal | str,
    remaining_lots: Decimal | str,
    realized_pnl: Decimal | str,
    reason: str,
    paper_mode: bool = False,
) -> None:
    """Append a ``partial-closed`` event referencing an existing ``open`` uuid."""
    if not reason:
        raise SchemaError("reason is required for partial-closed event")
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "uuid": uuid,
        "type": "partial-closed",
        "ts": _now_iso(),
        "closed_lots": _decimal_str(closed_lots, "closed_lots"),
        "remaining_lots": _decimal_str(remaining_lots, "remaining_lots"),
        "realized_pnl": _decimal_str(realized_pnl, "realized_pnl"),
        "reason": reason,
        "paper_mode": bool(paper_mode),
    }
    _append_line(path, record)


def write_close(
    path: Path | str,
    *,
    uuid: str,
    exit_price: Decimal | str,
    realized_pnl: Decimal | str,
    close_kind: str,
    reason: str,
    paper_mode: bool = False,
) -> None:
    """Append a ``closed`` event referencing an existing ``open`` uuid.

    ``close_kind`` is one of ``invalidation`` (thesis broken before SL hit) or
    ``manual`` (discretionary close not driven by structural invalidation).
    """
    if close_kind not in ALLOWED_CLOSE_KINDS:
        raise SchemaError(
            f"close_kind must be one of {ALLOWED_CLOSE_KINDS}, got {close_kind!r}"
        )
    if not reason:
        raise SchemaError("reason is required for closed event")
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "uuid": uuid,
        "type": "closed",
        "ts": _now_iso(),
        "exit_price": _decimal_str(exit_price, "exit_price"),
        "realized_pnl": _decimal_str(realized_pnl, "realized_pnl"),
        "close_kind": close_kind,
        "reason": reason,
        "paper_mode": bool(paper_mode),
    }
    _append_line(path, record)


def _append_line(path: Path | str, record: dict[str, Any]) -> None:
    """Append a JSON line, flush, fsync. Creates parent dirs if missing."""
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, separators=(",", ":"), ensure_ascii=False)
    # Open in binary append + fsync to survive a process crash without
    # losing the entry.
    with open(p, "ab") as f:
        f.write(line.encode("utf-8") + b"\n")
        f.flush()
        os.fsync(f.fileno())


# --- read ------------------------------------------------------------------


def read_raw(path: Path | str) -> list[dict[str, Any]]:
    """Stream all JSONL records as dicts, preserving order. Tolerates blanks."""
    p = Path(path).expanduser()
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    with open(p, "r", encoding="utf-8") as f:
        for line_no, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SchemaError(
                    f"line {line_no}: invalid JSON — {exc}"
                ) from exc
            if rec.get("schema_version") != SCHEMA_VERSION:
                raise SchemaError(
                    f"line {line_no}: unsupported schema_version "
                    f"{rec.get('schema_version')!r}; expected {SCHEMA_VERSION}"
                )
            out.append(rec)
    return out


def read_resolved(path: Path | str) -> list[dict[str, Any]]:
    """Read all entries, resolve update chains, return latest state per uuid.

    Order: by entry_time of the open record (chronological). Updates that
    reference a missing uuid are silently dropped — they don't have an
    open entry to patch, so they carry no meaning.
    """
    raw = read_raw(path)
    by_uuid: dict[str, dict[str, Any]] = {}
    update_buffer: dict[str, list[dict[str, Any]]] = {}
    for rec in raw:
        uid = rec.get("uuid")
        if not uid:
            continue
        if rec.get("type") == "open":
            by_uuid[uid] = dict(rec)
        elif rec.get("type") == "update":
            update_buffer.setdefault(uid, []).append(rec)

    for uid, updates in update_buffer.items():
        if uid not in by_uuid:
            continue  # orphan update, drop
        for upd in updates:
            for k, v in upd.items():
                if k in {"uuid", "type", "schema_version", "update_time"}:
                    continue
                by_uuid[uid][k] = v

    return sorted(
        by_uuid.values(),
        key=lambda r: r.get("entry_time", ""),
    )


_STAGE3_EVENT_TYPES = ("sl-trailed", "partial-closed", "closed")


def read_resolved_with_events(path: Path | str) -> list[dict[str, Any]]:
    """Like ``read_resolved`` but also attaches Stage 3 events under ``_events``.

    Each open record gets ``_events: list[dict]`` containing chronologically
    ordered ``sl-trailed`` / ``partial-closed`` / ``closed`` records that
    reference the open's uuid. Records with no Stage 3 events get an empty list.
    """
    resolved = {r["uuid"]: r for r in read_resolved(path)}
    raw = read_raw(path)
    events_by_uuid: dict[str, list[dict[str, Any]]] = {uid: [] for uid in resolved}
    for rec in raw:
        if rec.get("type") not in _STAGE3_EVENT_TYPES:
            continue
        uid = rec.get("uuid")
        if uid in events_by_uuid:
            events_by_uuid[uid].append(rec)
    for uid, events in events_by_uuid.items():
        events.sort(key=lambda e: e.get("ts", ""))
        resolved[uid]["_events"] = events
    return sorted(resolved.values(), key=lambda r: r.get("entry_time", ""))


def find_uuid_by_ticket(path: Path | str, ticket: int) -> Optional[str]:
    """Return the uuid of the latest ``open`` record with the given ticket, or None."""
    raw = read_raw(path)
    last_uid: Optional[str] = None
    for rec in raw:
        if rec.get("type") == "open" and rec.get("ticket") == ticket:
            last_uid = rec.get("uuid")
    return last_uid


# --- filter / suggest ------------------------------------------------------


def filter_resolved(
    entries: Iterable[dict[str, Any]],
    *,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    symbol: Optional[str] = None,
    setup_type: Optional[str] = None,
    side: Optional[str] = None,
    risk_classification: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Filter resolved entries by common dimensions.

    Period filters use ``exit_time`` (the trade is "completed" then). All
    string filters are exact-match for v1; fuzzy matching is a future skill.
    """
    out = list(entries)
    if since is not None:
        out = [e for e in out if datetime.fromisoformat(e["exit_time"]) >= since]
    if until is not None:
        out = [e for e in out if datetime.fromisoformat(e["exit_time"]) <= until]
    if symbol is not None:
        out = [e for e in out if e.get("symbol") == symbol]
    if setup_type is not None:
        out = [e for e in out if e.get("setup_type") == setup_type]
    if side is not None:
        if side not in ALLOWED_SIDES:
            raise SchemaError(f"side must be one of {ALLOWED_SIDES}")
        out = [e for e in out if e.get("side") == side]
    if risk_classification is not None:
        out = [e for e in out if e.get("risk_classification_at_close") == risk_classification]
    return out


def default_journal_path(account_id: Optional[str] = None) -> Path:
    """Resolve the journal path for an account_id, or the legacy root path.

    With account_id: ~/.trading-agent-skills/accounts/<id>/journal.jsonl
    Without: ~/.trading-agent-skills/journal.jsonl (backwards-compat for manual use)
    """
    base = Path.home() / ".trading-agent-skills"
    if account_id:
        from trading_agent_skills.account_paths import resolve_account_paths

        return resolve_account_paths(account_id=account_id).journal
    return base / "journal.jsonl"


def suggest_tags(path: Path | str) -> list[tuple[str, int]]:
    """Existing setup_type tags, ordered by frequency descending.

    Loaded into the agent's prompt to encourage tag consistency. The agent
    can present "did you mean..." when the user types something close to
    an existing tag.
    """
    entries = read_resolved(path)
    counts = Counter(e.get("setup_type") for e in entries if e.get("setup_type"))
    return counts.most_common()
