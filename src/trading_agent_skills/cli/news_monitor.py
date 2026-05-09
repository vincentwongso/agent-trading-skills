"""News-monitor CLI.

Polls Finnhub / Marketaux / ForexNews / AlphaVantage, classifies severity,
dedups against state file, emits new high-impact events as JSON to stdout.

Exit codes: 0 (success — events may be []), 1 (argument/runtime error).

Example::

    trading-agent-skills-news-monitor \\
        --state ~/.trading-agent-skills/accounts/7000522/news_seen.jsonl \\
        --lookback-minutes 10
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from trading_agent_skills.dotenv_loader import load_env_file
from trading_agent_skills.news_clients import (
    AlphaVantageNewsClient,
    FinnhubClient,
    ForexNewsClient,
    MarketauxClient,
)
from trading_agent_skills.news_monitor import (
    NewsMonitorInput,
    SeverityThresholds,
    monitor,
)


def _build_clients() -> dict:
    return {
        "finnhub": FinnhubClient(),
        "marketaux": MarketauxClient(),
        "forexnews": ForexNewsClient(),
        "alphavantage": AlphaVantageNewsClient(),
    }


def _serialise_event(event) -> dict:
    d = asdict(event)
    d["published_at_utc"] = event.published_at_utc.isoformat()
    return d


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="News monitor — emit high-impact PushEvents.")
    parser.add_argument("--state", type=Path, required=True,
                        help="Path to news_seen.jsonl state file.")
    parser.add_argument("--lookback-minutes", type=int, default=10,
                        help="Lookback window for fresh articles.")
    parser.add_argument("--state-ttl-hours", type=int, default=24,
                        help="How long state-file entries persist (dedup window).")
    parser.add_argument("--abs-sentiment", type=float, default=0.35,
                        help="Min |sentiment_score| to qualify as PUSH (sentiment-only path).")
    parser.add_argument("--relevance", type=float, default=0.5,
                        help="Min relevance_score to qualify as PUSH (sentiment-only path).")
    parser.add_argument("--env-file", type=Path, default=None,
                        help="Optional .env path (else searches default locations).")
    args = parser.parse_args(argv)

    # load_env_file requires a str|Path (not None); handle default-search manually.
    if args.env_file is not None:
        load_env_file(args.env_file)
    else:
        for candidate in (
            Path.home() / ".trading-agent-skills" / ".env",
            Path.cwd() / ".env",
        ):
            load_env_file(candidate)

    inp = NewsMonitorInput(
        now_utc=datetime.now(timezone.utc),
        lookback_minutes=args.lookback_minutes,
        state_path=args.state,
        state_ttl_hours=args.state_ttl_hours,
        thresholds=SeverityThresholds(
            abs_sentiment=args.abs_sentiment,
            relevance=args.relevance,
        ),
        clients=_build_clients(),
    )

    try:
        result = monitor(inp)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1

    output = {
        "events": [_serialise_event(e) for e in result.events],
        "provider_health": result.provider_health,
        "flags": result.flags,
    }
    print(json.dumps(output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
