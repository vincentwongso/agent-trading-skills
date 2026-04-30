"""CLI for the strategy-review skill — propose / apply.

Usage:
  trading-agent-skills-strategy-review propose --account-id <ID> --since <ISO> --until <ISO>
  echo '{"per_trade_risk_pct": 0.8}' | trading-agent-skills-strategy-review apply --account-id <ID>
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from trading_agent_skills.account_paths import resolve_account_paths
from trading_agent_skills.strategy_review import (
    apply_proposal,
    build_proposal_skeleton,
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="trading-agent-skills-strategy-review")
    sub = p.add_subparsers(dest="action", required=True)

    propose = sub.add_parser("propose", help="Generate a proposal skeleton.")
    propose.add_argument("--account-id", required=True)
    propose.add_argument("--since", required=True, help="ISO 8601, UTC")
    propose.add_argument("--until", required=True, help="ISO 8601, UTC")
    propose.set_defaults(func=_cmd_propose)

    apply = sub.add_parser("apply", help="Apply approved diff (JSON on stdin).")
    apply.add_argument("--account-id", required=True)
    apply.set_defaults(func=_cmd_apply)

    return p


def _cmd_propose(args: argparse.Namespace) -> int:
    paths = resolve_account_paths(account_id=args.account_id)
    paths.ensure_dirs()
    since = datetime.fromisoformat(args.since.replace("Z", "+00:00"))
    until = datetime.fromisoformat(args.until.replace("Z", "+00:00"))
    md = build_proposal_skeleton(paths, since=since, until=until)
    proposal_path = paths.proposals / f"{until.date().isoformat()}.md"
    proposal_path.write_text(md, encoding="utf-8")
    print(json.dumps({
        "status": "ok",
        "proposal_path": str(proposal_path),
    }))
    return 0


def _cmd_apply(args: argparse.Namespace) -> int:
    paths = resolve_account_paths(account_id=args.account_id)
    paths.ensure_dirs()
    diff = json.load(sys.stdin)
    try:
        new_charter = apply_proposal(paths, approved_changes=diff)
    except ValueError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps({
        "status": "ok",
        "new_version": new_charter.charter_version,
    }))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
