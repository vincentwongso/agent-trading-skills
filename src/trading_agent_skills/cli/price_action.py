"""CLI wrapper for ``trading_agent_skills.price_action.scan``.

Reads a JSON bundle from stdin (or ``--input <file>``) and writes the
``ScanResult`` as JSON to stdout. ``selected_setup_id`` /
``selection_rationale`` are always None on this side — the LLM in the
SKILL.md flow fills them after reading the candidate list.

Exit codes: 0 success, 1 schema error.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from trading_agent_skills.decimal_io import D
from trading_agent_skills.price_action import ScanInput, scan
from trading_agent_skills.price_action.scoring import DEFAULT_WEIGHTS, ScoringWeights


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


def _parse_weights(blob: dict[str, Any]) -> ScoringWeights:
    return ScoringWeights(
        confluence=D(blob["confluence"]),
        mtf_alignment=D(blob["mtf_alignment"]),
        candle_quality=D(blob["candle_quality"]),
        freshness=D(blob["freshness"]),
    )


def _build_input(bundle: dict[str, Any]) -> ScanInput:
    cfg = bundle.get("config") or {}
    weights = (
        _parse_weights(cfg["scoring_weights"])
        if "scoring_weights" in cfg
        else DEFAULT_WEIGHTS
    )
    quote = bundle["current_quote"]
    bid = D(quote["bid"])
    ask = D(quote["ask"])
    current_price = (bid + ask) / Decimal(2)
    meta = bundle["symbol_meta"]
    return ScanInput(
        symbol=bundle["symbol"],
        mode=bundle.get("mode", "swing"),
        timeframes=tuple(bundle["timeframes"]),
        rates_by_tf=bundle["rates"],
        current_price=current_price,
        tick_size=D(meta["tick_size"]),
        digits=int(meta["digits"]),
        as_of=datetime.fromisoformat(bundle["as_of"].replace("Z", "+00:00")),
        max_setups=int(cfg.get("max_setups", 3)),
        quality_threshold=D(cfg.get("quality_threshold", "0.45")),
        weights=weights,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trading-agent-skills-price-action")
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
        inp = _build_input(bundle)
    except (KeyError, TypeError, ValueError) as exc:
        print(f"ERROR: malformed input bundle: {exc}", file=sys.stderr)
        return 1

    result = scan(inp)
    json.dump(_to_jsonable(result), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
