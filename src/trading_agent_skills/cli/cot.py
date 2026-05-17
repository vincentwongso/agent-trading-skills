"""CLI for COT crowdedness scoring.

Subcommands:
- ``refresh``  fetch + cache one or all mapped symbols from CFTC Socrata API
- ``get``      compute crowdedness from cache (no network), JSON to stdout
- ``list``     list mapped symbols + contract codes

Exit codes: 0 success, 1 schema/IO error, 2 network error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from trading_agent_skills.cot_crowdedness import (
    DEFAULT_CACHE_DIR,
    DEFAULT_LOOKBACK_WEEKS,
    SYMBOL_TO_CFTC,
    compute_crowdedness,
    load_cache,
    refresh_symbol,
)


def _cmd_list(args: argparse.Namespace) -> int:
    out = {
        "symbols": [
            {
                "symbol": sym,
                "contract_code": c.code,
                "contract_label": c.label,
                "exchange": c.exchange,
                "note": c.note,
            }
            for sym, c in sorted(SYMBOL_TO_CFTC.items())
        ]
    }
    print(json.dumps(out, indent=2))
    return 0


def _cmd_refresh(args: argparse.Namespace) -> int:
    cache_dir = Path(args.cache_dir) if args.cache_dir else DEFAULT_CACHE_DIR
    targets = [args.symbol] if args.symbol else sorted(SYMBOL_TO_CFTC)
    results = []
    rc = 0
    for sym in targets:
        try:
            path, n = refresh_symbol(sym, weeks=args.weeks, cache_dir=cache_dir)
            results.append({"symbol": sym, "cached_at": str(path), "n_entries": n})
        except Exception as exc:  # noqa: BLE001 — surface to caller, partial success allowed
            results.append({"symbol": sym, "error": f"{type(exc).__name__}: {exc}"})
            rc = 2
    print(json.dumps({"refresh": results}, indent=2))
    return rc


def _cmd_get(args: argparse.Namespace) -> int:
    cache_dir = Path(args.cache_dir) if args.cache_dir else DEFAULT_CACHE_DIR
    entries = load_cache(args.symbol, cache_dir=cache_dir)
    if not entries:
        print(json.dumps({
            "error": "no_cache",
            "symbol": args.symbol,
            "hint": f"run: trading-agent-skills-cot refresh --symbol {args.symbol}",
        }))
        return 1
    snap = compute_crowdedness(
        args.symbol,
        entries,
        lookback_weeks=args.lookback_weeks,
    )
    print(json.dumps(snap.as_dict(), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="trading-agent-skills-cot")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="list mapped symbols + CFTC codes")
    p_list.set_defaults(func=_cmd_list)

    p_refresh = sub.add_parser("refresh", help="fetch + cache from CFTC Socrata")
    p_refresh.add_argument("--symbol", help="single symbol; omit for all mapped")
    p_refresh.add_argument(
        "--weeks", type=int, default=DEFAULT_LOOKBACK_WEEKS,
        help=f"weeks of history to fetch (default {DEFAULT_LOOKBACK_WEEKS})",
    )
    p_refresh.add_argument("--cache-dir", help="override default cache dir")
    p_refresh.set_defaults(func=_cmd_refresh)

    p_get = sub.add_parser("get", help="compute crowdedness from cache (no network)")
    p_get.add_argument("--symbol", required=True)
    p_get.add_argument(
        "--lookback-weeks", type=int, default=DEFAULT_LOOKBACK_WEEKS,
        help="lookback window for percentile rank",
    )
    p_get.add_argument("--cache-dir", help="override default cache dir")
    p_get.set_defaults(func=_cmd_get)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
