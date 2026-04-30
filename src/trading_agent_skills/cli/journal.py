"""CLI for the trade journal — write / update / query / stats / tags.

Subcommand entrypoint::

    trading-agent-skills-journal <subcommand> [--journal-path PATH] [...]

``write`` and ``update`` consume a JSON bundle on stdin (or via ``-i FILE``).
The other subcommands print JSON to stdout for the agent to render.

Default journal path: ``~/.trading-agent-skills/journal.jsonl``. The skill's bash
invocation can override with ``--journal-path``; tests use ``tmp_path``.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from trading_agent_skills.decision_log import (
    DecisionSchemaError,
    filter_decisions,
    write_intent,
    write_outcome,
)
from trading_agent_skills.journal_io import (
    SchemaError,
    default_journal_path,
    filter_resolved,
    read_resolved,
    suggest_tags,
    write_open,
    write_update,
)
from trading_agent_skills.journal_stats import (
    Summary,
    by_risk_classification,
    by_setup_type,
    by_side,
    by_symbol,
    compute_summary,
    swing_subset,
)


DEFAULT_PATH = Path("~/.trading-agent-skills/journal.jsonl")


def _resolve_journal_path(args: argparse.Namespace) -> Path:
    """Resolution: explicit --journal-path wins; else --account-id; else legacy."""
    if getattr(args, "journal_path", None) is not None:
        return args.journal_path
    return default_journal_path(account_id=getattr(args, "account_id", None))


# --- helpers ---------------------------------------------------------------


def _to_jsonable(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return format(obj, "f")
    if isinstance(obj, Summary):
        return obj.to_dict()
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    return obj


def _resolve_period(period: str | None) -> tuple[Optional[datetime], Optional[datetime]]:
    """Map a shorthand period to (since, until) UTC datetimes."""
    if period is None or period == "all":
        return None, None
    now = datetime.now(timezone.utc)
    if period == "today":
        # 00:00 UTC of today.
        since = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        since = now - timedelta(days=7)
    elif period == "month":
        since = now - timedelta(days=30)
    else:
        raise SchemaError(f"unknown period {period!r}; use today|week|month|all")
    return since, now


def _add_filter_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--period", choices=["today", "week", "month", "all"], default=None)
    p.add_argument("--since", help="ISO 8601 (overrides --period start)")
    p.add_argument("--until", help="ISO 8601 (overrides --period end)")
    p.add_argument("--symbol")
    p.add_argument("--setup-type")
    p.add_argument("--side", choices=["buy", "sell"])
    p.add_argument("--risk-classification", choices=["AT_RISK", "RISK_FREE", "LOCKED_PROFIT"])


def _apply_filter_args(entries: list[dict], args: argparse.Namespace) -> list[dict]:
    since, until = _resolve_period(args.period)
    if args.since:
        since = datetime.fromisoformat(args.since)
    if args.until:
        until = datetime.fromisoformat(args.until)
    return filter_resolved(
        entries,
        since=since, until=until,
        symbol=args.symbol,
        setup_type=args.setup_type,
        side=args.side,
        risk_classification=args.risk_classification,
    )


def _read_stdin_or_file(path: str) -> str:
    return sys.stdin.read() if path == "-" else open(path, encoding="utf-8").read()


# --- subcommands -----------------------------------------------------------


def cmd_write(args: argparse.Namespace) -> int:
    journal_path = _resolve_journal_path(args)
    raw = _read_stdin_or_file(args.input)
    try:
        bundle = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"ERROR: invalid JSON: {exc}", file=sys.stderr)
        return 1
    try:
        uid = write_open(journal_path, **bundle)
    except (TypeError, SchemaError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.json:
        json.dump({"uuid": uid}, sys.stdout)
        sys.stdout.write("\n")
    else:
        print(f"Wrote entry {uid} to {journal_path}")
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    journal_path = _resolve_journal_path(args)
    raw = _read_stdin_or_file(args.input)
    try:
        bundle = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"ERROR: invalid JSON: {exc}", file=sys.stderr)
        return 1
    if "uuid" not in bundle:
        print("ERROR: 'uuid' is required for update", file=sys.stderr)
        return 1
    try:
        write_update(journal_path, **bundle)
    except (TypeError, SchemaError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.json:
        json.dump({"uuid": bundle["uuid"], "updated": True}, sys.stdout)
        sys.stdout.write("\n")
    else:
        print(f"Patched entry {bundle['uuid']}")
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    entries = read_resolved(_resolve_journal_path(args))
    filtered = _apply_filter_args(entries, args)
    if args.swing_only:
        filtered = swing_subset(filtered)
    json.dump({"count": len(filtered), "entries": filtered}, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    entries = read_resolved(_resolve_journal_path(args))
    filtered = _apply_filter_args(entries, args)
    if args.swing_only:
        filtered = swing_subset(filtered)

    payload: dict[str, Any] = {
        "count": len(filtered),
        "summary": compute_summary(filtered).to_dict(),
    }
    if args.group_by in ("setup_type", "all"):
        payload["by_setup_type"] = {k: v.to_dict() for k, v in by_setup_type(filtered).items()}
    if args.group_by in ("symbol", "all"):
        payload["by_symbol"] = {k: v.to_dict() for k, v in by_symbol(filtered).items()}
    if args.group_by in ("side", "all"):
        payload["by_side"] = {k: v.to_dict() for k, v in by_side(filtered).items()}
    if args.group_by in ("risk_classification", "all"):
        payload["by_risk_classification"] = {
            k: v.to_dict() for k, v in by_risk_classification(filtered).items()
        }
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_tags(args: argparse.Namespace) -> int:
    tags = suggest_tags(_resolve_journal_path(args))
    json.dump({"tags": [{"setup_type": t, "count": c} for t, c in tags]}, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


# --- decision log subcommands ---------------------------------------------


def _read_decision_payload() -> dict[str, Any]:
    raw = sys.stdin.read()
    return json.loads(raw)


def cmd_decision_write_intent(args: argparse.Namespace) -> int:
    try:
        payload = _read_decision_payload()
    except json.JSONDecodeError as exc:
        print(json.dumps({"status": "error", "error": f"invalid JSON: {exc}"}),
              file=sys.stderr)
        return 2
    try:
        rec = write_intent(args.decisions_path, **payload)
    except (DecisionSchemaError, TypeError, KeyError) as exc:
        print(json.dumps({
            "status": "error",
            "error": str(exc),
            "kind": payload.get("kind") if isinstance(payload, dict) else None,
        }), file=sys.stderr)
        return 2
    print(json.dumps({"status": "ok", "record": rec}))
    return 0


def cmd_decision_write_outcome(args: argparse.Namespace) -> int:
    try:
        payload = _read_decision_payload()
    except json.JSONDecodeError as exc:
        print(json.dumps({"status": "error", "error": f"invalid JSON: {exc}"}),
              file=sys.stderr)
        return 2
    try:
        rec = write_outcome(args.decisions_path, **payload)
    except (DecisionSchemaError, TypeError, KeyError) as exc:
        print(json.dumps({
            "status": "error",
            "error": str(exc),
            "kind": payload.get("kind") if isinstance(payload, dict) else None,
        }), file=sys.stderr)
        return 2
    print(json.dumps({"status": "ok", "record": rec}))
    return 0


def cmd_decision_read(args: argparse.Namespace) -> int:
    since_dt = None
    if args.since:
        since_dt = datetime.fromisoformat(args.since.replace("Z", "+00:00"))
    records = list(filter_decisions(
        args.decisions_path, since=since_dt, kind=args.kind, symbol=args.symbol
    ))
    print(json.dumps({"records": records}))
    return 0


# --- entry point ----------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trading-agent-skills-journal")
    parser.add_argument(
        "--journal-path",
        type=lambda s: Path(s).expanduser(),
        default=None,
        help="Path to journal.jsonl (default: per-account or ~/.trading-agent-skills/journal.jsonl)",
    )
    parser.add_argument(
        "--account-id",
        type=str,
        default=None,
        help="If set, journal is read/written under accounts/<id>/journal.jsonl",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_write = sub.add_parser("write", help="Append a new open entry from JSON stdin/file")
    p_write.add_argument("--input", "-i", default="-")
    p_write.add_argument("--json", action="store_true", help="Emit JSON instead of human text")
    p_write.set_defaults(func=cmd_write)

    p_update = sub.add_parser("update", help="Append a patch to an existing entry by uuid")
    p_update.add_argument("--input", "-i", default="-")
    p_update.add_argument("--json", action="store_true")
    p_update.set_defaults(func=cmd_update)

    p_query = sub.add_parser("query", help="List filtered entries as JSON")
    _add_filter_args(p_query)
    p_query.add_argument("--swing-only", action="store_true",
                         help="Restrict to carry-driven trades (see journal_stats.swing_subset)")
    p_query.set_defaults(func=cmd_query)

    p_stats = sub.add_parser("stats", help="Summary stats over filtered entries")
    _add_filter_args(p_stats)
    p_stats.add_argument(
        "--group-by",
        choices=["setup_type", "symbol", "side", "risk_classification", "all"],
        default=None,
    )
    p_stats.add_argument("--swing-only", action="store_true")
    p_stats.set_defaults(func=cmd_stats)

    p_tags = sub.add_parser("tags", help="List existing setup_type tags by frequency")
    p_tags.set_defaults(func=cmd_tags)

    # decision log: nested subcommand `decision {write,write-outcome}`.
    # Uses --decisions-path (separate from --journal-path) because the
    # autonomous-loop decision log lives in a different file.
    p_decision = sub.add_parser(
        "decision", help="Decision log read/write (autonomous mode)."
    )
    decision_sub = p_decision.add_subparsers(dest="decision_action", required=True)

    p_dec_write = decision_sub.add_parser(
        "write", help="Append a decision-intent record from JSON stdin."
    )
    p_dec_write.add_argument(
        "--decisions-path",
        type=lambda s: Path(s).expanduser(),
        required=True,
    )
    p_dec_write.set_defaults(func=cmd_decision_write_intent)

    p_dec_outcome = decision_sub.add_parser(
        "write-outcome", help="Append an outcome record from JSON stdin."
    )
    p_dec_outcome.add_argument(
        "--decisions-path",
        type=lambda s: Path(s).expanduser(),
        required=True,
    )
    p_dec_outcome.set_defaults(func=cmd_decision_write_outcome)

    p_dec_read = decision_sub.add_parser(
        "read", help="Read reconciled decision records, JSON to stdout."
    )
    p_dec_read.add_argument(
        "--decisions-path",
        type=lambda s: Path(s).expanduser(),
        required=True,
    )
    p_dec_read.add_argument(
        "--since", type=str, default=None,
        help="ISO 8601 cutoff; records older than this are excluded.",
    )
    p_dec_read.add_argument(
        "--kind", type=str, default=None,
        choices=["open", "modify", "close", "skip", "mode_change"],
    )
    p_dec_read.add_argument("--symbol", type=str, default=None)
    p_dec_read.set_defaults(func=cmd_decision_read)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
