"""CLI wrapper for AlphaVantage options-OI crowdedness scoring.

JSON-stdin / JSON-stdout — the agent (Claude Code) is expected to fan out
``mcp__alphavantage__HISTORICAL_OPTIONS`` (and friends), bundle the
response, and pipe it here. No HTTP, no MCP client imports.

Bundle shape (stdin or ``--input <file>``)::

    {
      "symbol": "SPX500",
      "as_of": "2026-05-17T16:00:00+00:00",
      "options_chain": { ...AV MCP options blob, with "data": [{...}, ...] },
      "history": [
        {"as_of": "2026-04-19T16:00:00+00:00", "put_call_oi_ratio": "1.42"},
        ...
      ],
      "pin_risk_days": 7
    }

Output: the ``Crowdedness.as_dict()`` JSON (``contract_code`` prefixed
``avopt:<symbol>``, optionally ``:pin``).

Exit codes:
- 0  success
- 1  schema / parse / compute error
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from trading_agent_skills.decimal_io import D
from trading_agent_skills.options_crowdedness import (
    DEFAULT_PIN_RISK_DAYS,
    OptionsChainSnapshot,
    OptionsHistoryEntry,
    compute_crowdedness,
)


def _parse_dt(value: Any) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _load_bundle(args: argparse.Namespace) -> dict[str, Any]:
    if args.input:
        with open(args.input, "r", encoding="utf-8") as fh:
            raw = fh.read()
    else:
        raw = sys.stdin.read()
    if not raw.strip():
        raise ValueError("empty bundle on stdin")
    return json.loads(raw)


def _build_snapshot(bundle: dict[str, Any]) -> OptionsChainSnapshot:
    symbol = str(bundle.get("symbol") or "")
    if not symbol:
        raise ValueError("bundle missing 'symbol'")
    chain = bundle.get("options_chain")
    if not isinstance(chain, dict):
        raise ValueError("bundle missing 'options_chain' object")

    # Stamp the snapshot with the bundle-level as_of/symbol when absent.
    chain = dict(chain)
    chain.setdefault("symbol", symbol)
    if "as_of" in bundle and "as_of" not in chain:
        chain["as_of"] = bundle["as_of"]
    return OptionsChainSnapshot.from_av_chain(chain)


def _build_history(bundle: dict[str, Any]) -> list[OptionsHistoryEntry]:
    raw = bundle.get("history") or []
    if not isinstance(raw, list):
        raise ValueError("'history' must be a list")
    entries: list[OptionsHistoryEntry] = []
    for i, row in enumerate(raw):
        if not isinstance(row, dict):
            raise ValueError(f"history[{i}] is not an object")
        as_of = _parse_dt(row.get("as_of"))
        ratio_raw = row.get("put_call_oi_ratio")
        if ratio_raw is None:
            raise ValueError(f"history[{i}] missing 'put_call_oi_ratio'")
        entries.append(OptionsHistoryEntry(
            as_of=as_of,
            put_call_oi_ratio=D(ratio_raw),
        ))
    # Sort oldest-first so the "growing" delta walk is in chronological order.
    entries.sort(key=lambda e: e.as_of)
    return entries


def _run(args: argparse.Namespace) -> int:
    try:
        bundle = _load_bundle(args)
        symbol = str(bundle.get("symbol") or "")
        snapshot = _build_snapshot(bundle)
        history = _build_history(bundle)
        pin_risk_days = int(bundle.get("pin_risk_days") or DEFAULT_PIN_RISK_DAYS)
        snap = compute_crowdedness(
            symbol,
            snapshot,
            history,
            pin_risk_days=pin_risk_days,
        )
    except (ValueError, KeyError, TypeError, json.JSONDecodeError, ArithmeticError) as exc:
        print(json.dumps({
            "error": "schema_error",
            "message": f"{type(exc).__name__}: {exc}",
        }))
        return 1
    print(json.dumps(snap.as_dict(), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="trading-agent-skills-options-crowdedness",
        description="Score put/call OI crowdedness from an AV-MCP bundle.",
    )
    p.add_argument(
        "--input",
        help="path to a JSON bundle file (defaults to stdin)",
        default=None,
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return _run(args)


if __name__ == "__main__":
    sys.exit(main())
