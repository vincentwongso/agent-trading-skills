"""CLI wrapper for ``cfd_skills.news_brief.build``.

Reads a JSON request bundle from stdin, optionally fans out to the three
news providers (Finnhub / Marketaux / ForexNews) when ``news`` is absent
from the bundle, runs the orchestrator, and writes a JSON
``NewsBriefResult`` to stdout.

Bundle shape (all keys except ``account``/``now_utc`` are optional)::

    {
      "now_utc": "2026-04-29T21:00:00+00:00",
      "lookahead_hours": 4,
      "lookback_hours": 12,

      "explicit_watchlist": ["XAUUSD"],
      "open_position_symbols": ["UKOIL"],
      "volatility_ranked": ["XAGUSD", "BTCUSD"],
      "calendar_event_currencies": ["USD", "EUR"],
      "earnings_constituent_indices": ["NAS100"],
      "max_size": 8,

      "symbol_meta": {
        "XAUUSD": {
          "currency_base": "XAU",
          "currency_profit": "USD",
          "category": "metals",
          "swap_long": "125",
          "swap_short": "-150"
        }
      },

      "bars_by_symbol": {
        "XAUUSD": [{"time": "...", "open": "...", "high": "...",
                     "low": "...", "close": "...", "volume": 0}, ...]
      },

      "calix": {
        "economic_events": [...],     # raw Calix /v1/calendar/economic shapes
        "earnings_entries": [...],
        "economic_stale": false,
        "earnings_stale": false
      },

      # If absent, the CLI fans out to the three news providers itself.
      "news": {
        "articles_by_provider": {
          "finnhub": [<NewsArticle dicts>],
          "marketaux": [...],
          "forexnews": [...]
        },
        "provider_status": {"finnhub": "ok", ...}
      },

      "config_path": "/path/to/config.toml"
    }

Exit codes: 0 success, 1 schema error.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping

from cfd_skills.checklist import CalixEarningsEntry, CalixEconomicEvent
from cfd_skills.config_io import DEFAULT_CONFIG_PATH, load_config
from cfd_skills.decimal_io import D
from cfd_skills.dotenv_loader import load_env_file
from cfd_skills.indicators import Bar, bars_from_mcp
from cfd_skills.news_brief import (
    DEFAULT_CALENDAR_LOOKAHEAD_HOURS,
    DEFAULT_NEWS_LOOKBACK_HOURS,
    NewsBriefInput,
    SymbolMeta,
    build,
)
from cfd_skills.news_clients import (
    FinnhubClient,
    ForexNewsClient,
    MarketauxClient,
)
from cfd_skills.news_dedup import NewsArticle, canonicalise_url, classify_impact
from cfd_skills.watchlist import (
    calendar_driven_symbols,
    resolve_watchlist,
)


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
    parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _parse_symbol_meta(blob: dict[str, Any]) -> dict[str, SymbolMeta]:
    out: dict[str, SymbolMeta] = {}
    for sym, m in blob.items():
        out[sym] = SymbolMeta(
            symbol=sym,
            currency_base=str(m.get("currency_base", "")),
            currency_profit=str(m.get("currency_profit", "")),
            category=str(m.get("category", "")),
            swap_long=D(m.get("swap_long", "0")),
            swap_short=D(m.get("swap_short", "0")),
        )
    return out


def _parse_bars(blob: dict[str, Any]) -> dict[str, list[Bar]]:
    return {sym: bars_from_mcp(b) for sym, b in blob.items()}


def _parse_article(blob: dict[str, Any]) -> NewsArticle:
    raw_pub = blob.get("published_at_utc") or blob.get("published_at")
    if isinstance(raw_pub, datetime):
        published = raw_pub
    elif raw_pub:
        published = datetime.fromisoformat(str(raw_pub).replace("Z", "+00:00"))
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
    else:
        published = datetime.now(timezone.utc)
    title = str(blob.get("title", ""))
    summary = str(blob.get("summary", ""))
    url = str(blob.get("url", ""))
    return NewsArticle(
        title=title,
        summary=summary,
        url=url,
        canonical_url=str(blob.get("canonical_url") or canonicalise_url(url)),
        published_at_utc=published,
        source=str(blob.get("source", "")),
        publisher=str(blob.get("publisher", "")),
        symbols=tuple(str(s) for s in blob.get("symbols", ())),
        keywords=tuple(str(k) for k in blob.get("keywords", ())),
        impact=str(blob.get("impact") or classify_impact(title, summary)),
    )


def _is_equity_like_ticker(symbol: str) -> bool:
    """Marketaux's ``symbols`` filter is intended for equity/index tickers
    (AAPL, MSFT, SPY, ^GSPC...). Broker CFD names like ``XAUUSD.z`` or
    ``USOIL`` aren't in its entity space — passing them returns zero
    articles instead of broadening to the general feed. Heuristic: short
    all-alpha all-uppercase token with no digits or dots."""
    if not (1 <= len(symbol) <= 5):
        return False
    return symbol.isalpha() and symbol.isupper()


def _derive_currencies_from_meta(
    watchlist_symbols: Iterable[str],
    symbol_meta: Mapping[str, "SymbolMeta"],
) -> set[str]:
    """Use the broker-supplied currency_base/currency_profit on each symbol's
    meta — robust to suffix forms (``XAUUSD.z``) that the old length-6
    heuristic missed."""
    out: set[str] = set()
    for sym in watchlist_symbols:
        meta = symbol_meta.get(sym)
        if meta is None:
            continue
        if meta.currency_base and len(meta.currency_base) == 3:
            out.add(meta.currency_base.upper())
        if meta.currency_profit and len(meta.currency_profit) == 3:
            out.add(meta.currency_profit.upper())
    return out


def _fan_out_news(
    *,
    watchlist_symbols: list[str],
    symbol_meta: Mapping[str, "SymbolMeta"],
    lookback_hours: int,
) -> tuple[dict[str, list[NewsArticle]], dict[str, str]]:
    finnhub = FinnhubClient()
    marketaux = MarketauxClient()
    forexnews = ForexNewsClient()
    fin_a, fin_s = finnhub.fetch_general(lookback_hours=lookback_hours)
    # Marketaux: filter only to symbols that look like equity tickers it can
    # recognize. CFD broker names (XAUUSD.z, USOIL) drop through to the
    # general feed where downstream relevance matching does the picking.
    equity_tickers = [s for s in watchlist_symbols if _is_equity_like_ticker(s)]
    mark_a, mark_s = marketaux.fetch(
        symbols=equity_tickers, lookback_hours=lookback_hours
    )
    # ForexNews: derive ISO currency codes from broker meta rather than
    # parsing the ticker. Handles suffix forms (XAUUSD.z) cleanly.
    currencies = _derive_currencies_from_meta(watchlist_symbols, symbol_meta)
    fx_a, fx_s = forexnews.fetch(currencies=sorted(currencies))
    return (
        {"finnhub": fin_a, "marketaux": mark_a, "forexnews": fx_a},
        {"finnhub": fin_s, "marketaux": mark_s, "forexnews": fx_s},
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cfd-skills-news")
    parser.add_argument(
        "--input", "-i", default="-",
        help="Path to JSON input file ('-' for stdin; default: -).",
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help=(
            "Optional path to a .env file with FINNHUB_API_KEY / "
            "MARKETAUX_API_KEY / FOREXNEWS_API_KEY. Defaults to "
            "~/.cfd-skills/.env then ./.env in the working directory. "
            "Real shell env vars always win."
        ),
    )
    args = parser.parse_args(argv)

    # Load .env (if present). Real env vars win — load_env_file uses setdefault.
    if args.env_file is not None:
        load_env_file(args.env_file)
    else:
        for candidate in (Path.home() / ".cfd-skills" / ".env", Path.cwd() / ".env"):
            load_env_file(candidate)

    raw = sys.stdin.read() if args.input == "-" else open(args.input, encoding="utf-8").read()
    try:
        bundle = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"ERROR: invalid JSON on stdin: {exc}", file=sys.stderr)
        return 1

    try:
        config_path = Path(bundle.get("config_path", DEFAULT_CONFIG_PATH))
        config = load_config(config_path, write_default_if_missing=True)
        now_utc = _parse_now(bundle)

        lookahead = int(bundle.get("lookahead_hours", DEFAULT_CALENDAR_LOOKAHEAD_HOURS))
        lookback = int(bundle.get("lookback_hours", DEFAULT_NEWS_LOOKBACK_HOURS))
        max_size = int(bundle.get("max_size", config.watchlist.max_size))

        explicit = list(bundle.get("explicit_watchlist") or [])
        open_pos = list(bundle.get("open_position_symbols") or [])
        cal_currencies = list(bundle.get("calendar_event_currencies") or [])
        earnings_idx = list(bundle.get("earnings_constituent_indices") or [])
        vol_ranked = list(bundle.get("volatility_ranked") or [])

        symbol_meta = _parse_symbol_meta(bundle.get("symbol_meta", {}))
        bars_by_symbol = _parse_bars(bundle.get("bars_by_symbol", {}))

        # The calendar-driven tier is sourced from a hard-coded editorial
        # ``_CURRENCY_TO_SYMBOLS`` map (e.g. USD → XAUUSD/XAGUSD/NAS100/...).
        # Those names may not match the broker's actual symbol catalog
        # (Fintrix uses ``XAUUSD.z``, not ``XAUUSD``). Intersect with the
        # bundle's ``symbol_meta`` keys — that's the broker-confirmed catalog
        # the agent has bars for. Falls back to the configured base_universe
        # only if the bundle didn't carry any meta.
        if symbol_meta:
            broker_catalog: tuple[str, ...] | None = tuple(symbol_meta.keys())
        else:
            broker_catalog = config.watchlist.base_universe

        cal_driven = list(calendar_driven_symbols(
            economic_event_currencies=cal_currencies,
            earnings_constituents_for_indices=earnings_idx,
            base_universe=broker_catalog,
        ))

        watchlist_res = resolve_watchlist(
            explicit=explicit,
            open_position_symbols=open_pos,
            calendar_symbols=cal_driven,
            volatility_ranked=vol_ranked,
            default=config.watchlist.default,
            max_size=max_size,
        )

        calix_blob = bundle.get("calix", {})
        economic_events = [
            CalixEconomicEvent.from_blob(b)
            for b in calix_blob.get("economic_events", [])
        ]
        earnings_entries = [
            CalixEarningsEntry.from_blob(b)
            for b in calix_blob.get("earnings_entries", [])
        ]
        economic_stale = bool(calix_blob.get("economic_stale", False))
        earnings_stale = bool(calix_blob.get("earnings_stale", False))

        news_blob = bundle.get("news")
        if news_blob is not None:
            articles_by_provider = {
                provider: [_parse_article(a) for a in arts]
                for provider, arts in news_blob.get("articles_by_provider", {}).items()
            }
            provider_status = dict(news_blob.get("provider_status", {}))
        else:
            articles_by_provider, provider_status = _fan_out_news(
                watchlist_symbols=list(watchlist_res.symbols),
                symbol_meta=symbol_meta,
                lookback_hours=lookback,
            )
    except (KeyError, TypeError, ValueError) as exc:
        print(f"ERROR: malformed input bundle: {exc}", file=sys.stderr)
        return 1

    inp = NewsBriefInput(
        now_utc=now_utc,
        lookahead_hours=lookahead,
        lookback_hours=lookback,
        watchlist=watchlist_res,
        bars_by_symbol=bars_by_symbol,
        symbol_meta=symbol_meta,
        economic_events=economic_events,
        earnings_entries=earnings_entries,
        economic_stale=economic_stale,
        earnings_stale=earnings_stale,
        articles_by_provider=articles_by_provider,
        provider_status=provider_status,
    )
    result = build(inp)

    json.dump(_to_jsonable(result), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
