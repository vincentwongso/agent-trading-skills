"""CLI wrapper for ``cfd_skills.position_sizer.size``.

Reads a JSON request bundle from stdin (or ``--input <file>``) and writes
a JSON result to stdout. Designed to be invoked by the skill's bash
script after the agent has fetched the relevant MCP tool outputs.

Input shape::

    {
      "request": {
        "side": "long" | "short",
        "risk_pct": "1.0",            # OR "risk_amount": "100"
        "stop_points": 200,           # OR "stop_price": "1.0824"
        "nights": 0,
        "broker_margin": "108.24",    # optional, from calc_margin
        "margin_warning_pct": "30"
      },
      "account": <get_account_info output>,
      "quote": <get_quote output>,
      "symbol": <one entry from get_symbols output>
    }

Output shape: a JSON-serialised ``SizingResult`` (Decimals as strings).
Exit code is 0 on success, 1 on schema error, 2 on logic error.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from typing import Any

from cfd_skills.decimal_io import D
from cfd_skills.position_sizer import (
    AccountInfo,
    Quote,
    SizingRequest,
    SymbolInfo,
    size,
)


def _opt_d(v: Any) -> Decimal | None:
    return D(v) if v is not None else None


def _request_from_dict(blob: dict) -> SizingRequest:
    kwargs: dict[str, Any] = {"side": blob["side"]}
    for fld in ("risk_pct", "risk_amount", "stop_price", "stop_distance", "broker_margin"):
        if fld in blob and blob[fld] is not None:
            kwargs[fld] = D(blob[fld])
    if "stop_points" in blob and blob["stop_points"] is not None:
        kwargs["stop_points"] = int(blob["stop_points"])
    if "nights" in blob:
        kwargs["nights"] = int(blob["nights"])
    if "margin_warning_pct" in blob:
        kwargs["margin_warning_pct"] = D(blob["margin_warning_pct"])
    return SizingRequest(**kwargs)


def _to_jsonable(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return format(obj, "f")
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    if is_dataclass(obj) and not isinstance(obj, type):
        return _to_jsonable(asdict(obj))
    return obj


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cfd-skills-size")
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
        request = _request_from_dict(bundle["request"])
        account = AccountInfo.from_mcp(bundle["account"])
        quote = Quote.from_mcp(bundle["quote"])
        sym = SymbolInfo.from_mcp(bundle["symbol"])
    except (KeyError, TypeError, ValueError) as exc:
        print(f"ERROR: malformed input bundle: {exc}", file=sys.stderr)
        return 1

    try:
        result = size(request=request, account=account, quote=quote, sym=sym)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    json.dump(_to_jsonable(result), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
