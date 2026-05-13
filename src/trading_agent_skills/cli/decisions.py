"""CLI for the decision log — append / migrate-to-sqlite / export-jsonl.

The `append` subcommand is the prompt-facing chokepoint: Stage 1/2/3 prompts
shell out to `trading-agent-skills-decisions append --record-json '<json>'`
instead of appending JSON to $DECISIONS directly. This is what gives Phase B's
SQLite dual-write a single point of entry for prompt-emitted records.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from trading_agent_skills.decisions_io import (
    DecisionSchemaError,
    append as decisions_append,
)


def _read_record_json(value: str) -> dict:
    """`@-` reads from stdin; everything else is treated as inline JSON."""
    if value == "@-":
        return json.loads(sys.stdin.read())
    return json.loads(value)


def _cmd_append(args: argparse.Namespace) -> int:
    try:
        record = _read_record_json(args.record_json)
    except json.JSONDecodeError as exc:
        print(f"invalid JSON in --record-json: {exc}", file=sys.stderr)
        return 2
    try:
        decisions_append(Path(args.decisions_path).expanduser(), record)
    except DecisionSchemaError as exc:
        print(f"decision schema error: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_migrate_to_sqlite(args: argparse.Namespace) -> int:
    """Read decisions.jsonl line-by-line and idempotently backfill into trader.db.

    Records that fail _normalize (e.g., missing both `ts` and `timestamp`) are
    skipped with a stderr warning. Returns 0 even with invalid rows — the
    summary tells you what was skipped.
    """
    from trading_agent_skills.decisions_io import (
        _canonical_payload,
        _connect_and_init,
        _dedup_key,
        _normalize,
        _row_columns,
        _sibling_db_path,
    )

    path = Path(args.decisions_path).expanduser()
    if not path.exists():
        print(f"decisions.jsonl not found: {path}", file=sys.stderr)
        return 1

    inserted = 0
    duplicates = 0
    invalid = 0

    if args.dry_run:
        with path.open("r", encoding="utf-8") as f:
            for line_no, raw in enumerate(f, start=1):
                line = raw.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    _normalize(rec)
                    inserted += 1  # Would-be-inserted (assuming no duplicates).
                except (json.JSONDecodeError, DecisionSchemaError) as exc:
                    invalid += 1
                    print(f"line {line_no}: {exc}", file=sys.stderr)
        print(json.dumps({"inserted": inserted, "duplicates": duplicates, "invalid": invalid}))
        return 0

    db_path = _sibling_db_path(path)
    con = _connect_and_init(db_path)
    try:
        with path.open("r", encoding="utf-8") as f:
            for line_no, raw in enumerate(f, start=1):
                line = raw.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError as exc:
                    print(f"line {line_no}: invalid JSON — {exc}", file=sys.stderr)
                    invalid += 1
                    continue
                try:
                    normalized = _normalize(rec)
                except DecisionSchemaError as exc:
                    snippet = line[:80] + ("..." if len(line) > 80 else "")
                    print(f"line {line_no}: {exc} — {snippet}", file=sys.stderr)
                    invalid += 1
                    continue
                cols = _row_columns(path, normalized)
                canonical = _canonical_payload(normalized)
                dedup = _dedup_key(canonical)
                original_payload = json.dumps(
                    normalized, separators=(",", ":"), ensure_ascii=False
                )
                cur = con.execute(
                    """
                    INSERT OR IGNORE INTO decisions (
                        ts, record_type, fire, run_id, symbol, ticket_id,
                        tick_id, schema_version, account, paper_mode, is_outcome,
                        payload, dedup_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cols["ts"], cols["record_type"], cols["fire"], cols["run_id"],
                        cols["symbol"], cols["ticket_id"], cols["tick_id"],
                        cols["schema_version"], cols["account"],
                        cols["paper_mode"], cols["is_outcome"],
                        original_payload, dedup,
                    ),
                )
                if cur.rowcount == 1:
                    inserted += 1
                else:
                    duplicates += 1
        con.commit()
    finally:
        con.close()

    print(json.dumps({"inserted": inserted, "duplicates": duplicates, "invalid": invalid}))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="trading-agent-skills-decisions",
        description="Decision log CLI (append + migrate-to-sqlite + export-jsonl).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sp_append = sub.add_parser("append", help="Append one decision record (JSONL + SQLite).")
    sp_append.add_argument("--decisions-path", required=True)
    sp_append.add_argument(
        "--record-json",
        required=True,
        help="Inline JSON object, or `@-` to read from stdin.",
    )
    sp_append.set_defaults(func=_cmd_append)

    sp_migrate = sub.add_parser(
        "migrate-to-sqlite",
        help="Backfill decisions.jsonl into trader.db (idempotent).",
    )
    sp_migrate.add_argument("--decisions-path", required=True)
    sp_migrate.add_argument(
        "--dry-run", action="store_true",
        help="Parse and validate only; do not write to SQLite.",
    )
    sp_migrate.set_defaults(func=_cmd_migrate_to_sqlite)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
