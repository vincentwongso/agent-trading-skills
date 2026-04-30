"""CLI wrapper for ``trading_agent_skills.guardian.assess``.

Reads a JSON request bundle from stdin (or ``--input <file>``) and writes
a JSON ``GuardianResult`` to stdout. The agent assembles the bundle from
mt5-mcp outputs:

    {
      "now_utc": "2026-04-29T21:00:00+00:00",   # optional, defaults to now
      "account": <get_account_info output>,
      "positions": [
        {
          "position": <one entry from get_positions>,
          "symbol":   <matching entry from get_symbols>,
          "classification": "AT_RISK" | "RISK_FREE" | "LOCKED_PROFIT" | null,
          "classification_reason": "<short string from agent reasoning>"
        }, ...
      ],
      "realized_pnl_today": "0.00",      # agent sums get_history deals
      "config_path": "/path/to/config.toml",   # optional override
      "state_path":  "/path/to/state.json"     # optional override
    }

Exit codes: 0 success, 1 schema error, 2 logic error.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from trading_agent_skills.config_io import DEFAULT_CONFIG_PATH, load_config
from trading_agent_skills.daily_state import DEFAULT_STATE_PATH, tick
from trading_agent_skills.decimal_io import D
from trading_agent_skills.guardian import (
    AccountSnapshot,
    GuardianInput,
    assess,
)
from trading_agent_skills.risk_state import Position


def _to_jsonable(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return format(obj, "f")
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    if is_dataclass(obj) and not isinstance(obj, type):
        return _to_jsonable(asdict(obj))
    return obj


def _parse_now(blob: dict[str, Any]) -> datetime:
    raw = blob.get("now_utc")
    if raw is None:
        return datetime.now(timezone.utc)
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _build_positions(entries: list[dict[str, Any]]) -> list[Position]:
    out: list[Position] = []
    for entry in entries:
        out.append(
            Position.from_mcp(
                position=entry["position"],
                symbol=entry["symbol"],
                classification=entry.get("classification"),
                classification_reason=str(entry.get("classification_reason", "")),
            )
        )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trading-agent-skills-guardian")
    parser.add_argument(
        "--input", "-i", default="-",
        help="Path to JSON input file ('-' for stdin; default: -).",
    )
    args = parser.parse_args(argv)

    raw = sys.stdin.read() if args.input == "-" else open(args.input, encoding="utf-8").read()
    try:
        bundle = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"ERROR: invalid JSON on stdin: {exc}", file=sys.stderr)
        return 1

    try:
        config_path = Path(bundle.get("config_path", DEFAULT_CONFIG_PATH))
        state_path = Path(bundle.get("state_path", DEFAULT_STATE_PATH))
        config = load_config(config_path, write_default_if_missing=True)
        now_utc = _parse_now(bundle)
        account = AccountSnapshot.from_mcp(bundle["account"])
        positions = _build_positions(bundle.get("positions", []))
        realized = D(bundle.get("realized_pnl_today", "0"))
    except (KeyError, TypeError, ValueError) as exc:
        print(f"ERROR: malformed input bundle: {exc}", file=sys.stderr)
        return 1

    session = tick(
        now_utc=now_utc,
        current_equity=account.equity,
        reset_tz=config.session.reset_tz,
        reset_time=config.session.reset_time,
        path=state_path,
    )

    inp = GuardianInput(
        now_utc=now_utc,
        account=account,
        session_open_balance=session.state.session_open_balance,
        last_reset_utc=session.state.last_reset_utc,
        next_reset_utc=session.next_reset_utc,
        realized_pnl_today=realized,
        positions=positions,
        config=config.risk,
    )

    try:
        result = assess(inp)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    output = _to_jsonable(result)
    output["session_just_reset"] = session.just_reset
    json.dump(output, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
