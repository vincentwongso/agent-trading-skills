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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
