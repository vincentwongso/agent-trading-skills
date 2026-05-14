"""CLI for the calendar skill — economic / earnings, upcoming / past / find.

Subcommand surface::

  trading-agent-skills-calendar economic upcoming  [filters]
  trading-agent-skills-calendar economic past      [filters]
  trading-agent-skills-calendar economic find      --title SUBSTR [--currency CODE] [--date YYYY-MM-DD]
  trading-agent-skills-calendar earnings upcoming  [filters]
  trading-agent-skills-calendar earnings past      [filters]

Output is JSON on stdout. Exit codes:
  0  success (including empty results)
  2  Calix unreachable / non-2xx / non-JSON  (also: argparse default for bad args)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from trading_agent_skills.calendar import enrich_events, find_events
from trading_agent_skills.calix_client import (
    DEFAULT_CACHE_DIR,
    CalixClient,
    CalixUnavailable,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trading-agent-skills-calendar",
        description="Look up upcoming / past economic events and earnings via Calix.",
    )
    noun = parser.add_subparsers(dest="noun", required=True)

    # economic
    econ = noun.add_parser("economic", help="Economic calendar events")
    econ_verb = econ.add_subparsers(dest="verb", required=True)

    for verb_name in ("upcoming", "past"):
        sub = econ_verb.add_parser(verb_name, help=f"{verb_name.title()} economic events")
        sub.add_argument("--currencies", default="majors")
        sub.add_argument("--impact", default="High")
        sub.add_argument("--limit", type=int, default=10)
        sub.add_argument("--within-hours", type=int, default=None)
        sub.add_argument("--raw", action="store_true")

    find_sub = econ_verb.add_parser("find", help="Find a specific past event by title")
    find_sub.add_argument("--title", required=True)
    find_sub.add_argument("--currency", default=None)
    find_sub.add_argument("--impact", default="High")
    find_sub.add_argument("--date", default=None, help="YYYY-MM-DD")
    find_sub.add_argument("--days-back", type=int, default=7)
    find_sub.add_argument("--raw", action="store_true")

    # earnings
    earn = noun.add_parser("earnings", help="Earnings releases")
    earn_verb = earn.add_subparsers(dest="verb", required=True)

    for verb_name in ("upcoming", "past"):
        sub = earn_verb.add_parser(verb_name, help=f"{verb_name.title()} earnings")
        sub.add_argument("--symbols", default=None)
        sub.add_argument("--limit", type=int, default=10)
        sub.add_argument("--within-days", type=int, default=None)
        sub.add_argument("--raw", action="store_true")

    return parser


ClientFactory = Callable[[Path], CalixClient]


def _default_client_factory(cache_dir: Path) -> CalixClient:
    return CalixClient(cache_dir=cache_dir)


def _emit(payload: dict) -> int:
    print(json.dumps(payload, default=str))
    return 0


def _emit_calix_error(exc: CalixUnavailable) -> int:
    print(json.dumps({"error": str(exc), "source": "calix"}))
    return 2


def _filter_within_hours(payload: dict, hours: int | None, now_utc: datetime) -> dict:
    if hours is None or "events" not in payload:
        return payload
    cutoff_minutes = hours * 60
    payload = dict(payload)
    payload["events"] = [
        e for e in payload["events"]
        if e.get("minutes_until", 0) <= cutoff_minutes
    ]
    return payload


def _filter_within_days(payload: dict, days: int | None, now_utc: datetime) -> dict:
    if days is None or "earnings" not in payload:
        return payload
    payload = dict(payload)
    payload["earnings"] = [
        e for e in payload["earnings"]
        if e.get("days_until", 0) <= days
    ]
    return payload


def _impact_list(s: str) -> list[str]:
    return [tok.strip() for tok in s.split(",") if tok.strip()]


def run(
    argv: list[str],
    *,
    now_utc: datetime | None = None,
    client_factory: ClientFactory | None = None,
    cache_dir: Path | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    now = now_utc or datetime.now(timezone.utc)
    factory = client_factory or _default_client_factory
    client = factory(cache_dir or DEFAULT_CACHE_DIR)

    try:
        if args.noun == "economic" and args.verb == "upcoming":
            resp = client.fetch_economic(
                currencies=args.currencies,
                impact=_impact_list(args.impact),
                limit=args.limit,
            )
            if args.raw:
                return _emit(resp.payload)
            enriched = enrich_events(resp.payload, now_utc=now)
            enriched = _filter_within_hours(enriched, args.within_hours, now)
            return _emit(enriched)
        if args.noun == "economic" and args.verb == "past":
            resp = client.fetch_economic_past(
                currencies=args.currencies,
                impact=_impact_list(args.impact),
                limit=args.limit,
            )
            if args.raw:
                return _emit(resp.payload)
            enriched = enrich_events(resp.payload, now_utc=now)
            return _emit(enriched)
        if args.noun == "economic" and args.verb == "find":
            # Pass --currency and --impact through to the upstream request:
            # Calix's past endpoint caps at 25 results sorted newest-first, so
            # broadening to all-currencies/all-impact crowds out older events
            # before find_events can see them. Narrowing upstream keeps older
            # same-currency events inside the window.
            resp = client.fetch_economic_past(
                currencies=args.currency or "all",
                impact=_impact_list(args.impact),
                limit=25,
            )
            result = find_events(
                resp.payload.get("events", []),
                title=args.title,
                currency=args.currency,
                date=args.date,
            )
            result["fetched_at_utc"] = now.isoformat()
            result["source"] = resp.payload.get("source")
            result["stale"] = bool(resp.payload.get("stale", False))
            return _emit(result)
        if args.noun == "earnings" and args.verb == "upcoming":
            resp = client.fetch_earnings(
                symbols=args.symbols,
                limit=args.limit,
            )
            if args.raw:
                return _emit(resp.payload)
            enriched = enrich_events(resp.payload, now_utc=now)
            enriched = _filter_within_days(enriched, args.within_days, now)
            return _emit(enriched)
        if args.noun == "earnings" and args.verb == "past":
            resp = client.fetch_earnings_past(
                symbols=args.symbols,
                limit=args.limit,
            )
            if args.raw:
                return _emit(resp.payload)
            enriched = enrich_events(resp.payload, now_utc=now)
            return _emit(enriched)
    except CalixUnavailable as exc:
        return _emit_calix_error(exc)


def main() -> int:
    return run(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
