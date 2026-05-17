"""CLI for FXSSI retail-sentiment crowdedness scoring.

Subcommands:
- ``list``     list mapped symbols + FXSSI slugs
- ``refresh``  fetch + merge into cache (``--symbol`` optional, default all)
- ``get``      compute crowdedness from cache (no network), JSON to stdout

Exit codes: 0 success, 1 schema/IO error, 2 network error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from trading_agent_skills.retail_sentiment import (
    DEFAULT_CACHE_DIR,
    DEFAULT_ENDPOINT,
    DEFAULT_LONG_THRESHOLD,
    DEFAULT_SHORT_THRESHOLD,
    FXSSI_SYMBOL_MAP,
    compute_crowdedness,
    load_cache,
    refresh_symbol,
)


def _cmd_list(args: argparse.Namespace) -> int:
    out = {
        "symbols": [
            {"symbol": sym, "fxssi_slug": slug}
            for sym, slug in sorted(FXSSI_SYMBOL_MAP.items())
        ]
    }
    print(json.dumps(out, indent=2))
    return 0


def _cmd_refresh(args: argparse.Namespace) -> int:
    cache_dir = Path(args.cache_dir) if args.cache_dir else DEFAULT_CACHE_DIR
    targets = [args.symbol] if args.symbol else sorted(FXSSI_SYMBOL_MAP)
    results = []
    rc = 0
    for sym in targets:
        try:
            path, n = refresh_symbol(sym, endpoint=args.endpoint, cache_dir=cache_dir)
            results.append({"symbol": sym, "cached_at": str(path), "n_entries": n})
        except Exception as exc:  # noqa: BLE001 — surface to caller, partial success allowed
            results.append({"symbol": sym, "error": f"{type(exc).__name__}: {exc}"})
            rc = 2
    print(json.dumps({"refresh": results}, indent=2))
    return rc


def _cmd_get(args: argparse.Namespace) -> int:
    cache_dir = Path(args.cache_dir) if args.cache_dir else DEFAULT_CACHE_DIR
    if args.symbol not in FXSSI_SYMBOL_MAP:
        print(json.dumps({
            "error": "unmapped_symbol",
            "symbol": args.symbol,
            "hint": "run: trading-agent-skills-retail-sentiment list",
        }))
        return 1
    entries = load_cache(args.symbol, cache_dir=cache_dir)
    if not entries:
        print(json.dumps({
            "error": "no_cache",
            "symbol": args.symbol,
            "hint": f"run: trading-agent-skills-retail-sentiment refresh --symbol {args.symbol}",
        }))
        return 1
    try:
        snap = compute_crowdedness(
            args.symbol,
            entries,
            long_threshold=args.long_threshold,
            short_threshold=args.short_threshold,
        )
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({
            "error": "compute_failed",
            "symbol": args.symbol,
            "detail": f"{type(exc).__name__}: {exc}",
        }))
        return 1
    print(json.dumps(snap.as_dict(), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="trading-agent-skills-retail-sentiment")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="list mapped symbols + FXSSI slugs")
    p_list.set_defaults(func=_cmd_list)

    p_refresh = sub.add_parser("refresh", help="fetch + merge FXSSI snapshot(s) into cache")
    p_refresh.add_argument("--symbol", help="single symbol; omit for all mapped")
    p_refresh.add_argument(
        "--endpoint", default=DEFAULT_ENDPOINT,
        help=f"FXSSI endpoint base (default {DEFAULT_ENDPOINT}) — see TODO in module docstring",
    )
    p_refresh.add_argument("--cache-dir", help="override default cache dir")
    p_refresh.set_defaults(func=_cmd_refresh)

    p_get = sub.add_parser("get", help="compute crowdedness from cache (no network)")
    p_get.add_argument("--symbol", required=True)
    p_get.add_argument(
        "--long-threshold", type=lambda s: __import__("decimal").Decimal(s),
        default=DEFAULT_LONG_THRESHOLD,
        help=f"pct_long >= this → crowded_long (default {DEFAULT_LONG_THRESHOLD})",
    )
    p_get.add_argument(
        "--short-threshold", type=lambda s: __import__("decimal").Decimal(s),
        default=DEFAULT_SHORT_THRESHOLD,
        help=f"pct_short >= this → crowded_short (default {DEFAULT_SHORT_THRESHOLD})",
    )
    p_get.add_argument("--cache-dir", help="override default cache dir")
    p_get.set_defaults(func=_cmd_get)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
