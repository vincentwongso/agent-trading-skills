---
name: session-news-brief
description: Use when the user wants a session-start brief, asks what's happening on a specific symbol, requests overnight news that moved markets, or asks for swing-trade candidates. Triggers on phrases like "morning brief", "news brief for the session", "what's happening on EURUSD", "any news on [symbol] in the last [N] hours", "any swing setups today", "what's moving the metals overnight". Composes Calix calendar + 3-API news fan-out (Finnhub / Marketaux / ForexNews) + ATR/RSI swing-candidates section. Read-only / advisory — never executes.
---

# Session News Brief

Combines economic + earnings calendar (Calix), a fan-out across three news APIs with cross-publisher dedup, and a swing-candidates section that surfaces positive-carry setups at technical extremes (UKOIL with Strait of Hormuz tension oversold on D1, EURUSD overbought with negative-carry-on-long → potentially a short, etc.).

The skill never executes — output is informational. Use [`pre-trade-checklist`](../pre-trade-checklist/SKILL.md) before entering any trade the brief surfaces.

## Prerequisites & first-run setup

This is the most setup-heavy skill in the bundle. Before the first invocation, verify:

1. **`trading-agent-skills-news` CLI is on PATH.** Test with `trading-agent-skills-news --help`. If not found: "Install the Python package — from the agent-trading-skills repo, run `pip install -e .` in a venv your harness can see."
2. **`mt5-mcp` server is connected.** Verify with `mcp__mt5-mcp__ping`. The brief uses `get_positions` (tier-2 watchlist), `get_symbols`, and `get_rates` (D1 bars for ATR/RSI swing candidates).
3. **Calix is reachable** at `https://calix.fintrixmarkets.com`. If unreachable, the calendar overlay degrades with `CALIX_DEGRADED` and the brief still runs.
4. **At least one news API key is set as an environment variable.** Check via the agent's environment: `FINNHUB_API_KEY`, `MARKETAUX_API_KEY`, `FOREXNEWS_API_KEY`. Each one is independent — the brief works with any subset, missing keys surface as `MISSING_NEWS_API_KEY` flags. If **none** are set, walk the user through this:

   > "The news section needs at least one of these API keys (all have free tiers):
   > - Finnhub: https://finnhub.io
   > - Marketaux: https://marketaux.com
   > - ForexNews: https://forexnewsapi.com
   >
   > After signing up, set the key. **Recommended (cross-session, Windows-friendly):** create `~/.trading-agent-skills/.env` with one line per key, e.g. `FINNHUB_API_KEY=abc123`. The CLI auto-loads this file. Alternatively, export in your shell (`export FINNHUB_API_KEY=...` on bash, `$env:FINNHUB_API_KEY=\"...\"` on PowerShell)."

5. **`~/.trading-agent-skills/config.toml`** is auto-generated on first run. Default watchlist is `XAUUSD / XAGUSD / USOIL / UKOIL / NAS100`; the user can edit `watchlist.default` and `watchlist.base_universe`.

If (1) or (2) fail, walk the user through the fix before bundling MCP outputs. (4) is non-fatal — proceed and surface the flag in the result.

## When to invoke

Trigger phrases:
- "morning brief" / "news brief for the session"
- "what's happening on [symbol]"
- "any news on [symbol] in the last [N] hours"
- "any swing setups today"
- "show me overnight movers"

Don't invoke for routine market-data questions (`mt5-market-data`); don't invoke for sizing or pre-trade gating (use the dedicated skills).

## Inputs (collect from user if implied; both optional)

1. **Watchlist** — explicit list. If omitted, the skill resolves via the 5-tier order below.
2. **Lookback hours** — defaults to 12 (morning brief) / 1 (intra-session refresh).

## Watchlist resolution (deduped union, capped at `config.watchlist.max_size`)

1. **Explicit input** — what the user passed
2. **Open positions** — from `mcp__mt5-mcp__get_positions`
3. **Calendar-driven** — symbols whose currencies hit the next-8h Calix economic events; index symbols for index-constituent earnings
4. **Volatility-ranked** — top-N from `config.watchlist.base_universe` by ATR(14) on D1 expressed as % of price (compute via `get_rates(D1, 30)` per symbol)
5. **Static fallback** — `config.watchlist.default` (XAUUSD / XAGUSD / USOIL / UKOIL / NAS100 by default)

## Workflow

### 1. Gather symbol metadata + bars

For each candidate symbol (after watchlist resolution):
- `mcp__mt5-mcp__get_symbols(category=...)` → currency_base, currency_profit, swap_long, swap_short, category
- `mcp__mt5-mcp__get_rates(symbol, timeframe="D1", count=30)` → bars for ATR(14) + RSI(14) + EMA(20)

### 2. Gather Calix calendar

```bash
curl -s "https://calix.fintrixmarkets.com/v1/calendar/economic/upcoming?currencies=USD,EUR&impact=High&limit=20"
curl -s "https://calix.fintrixmarkets.com/v1/calendar/earnings/upcoming?limit=30"
```

If Calix is unreachable or returns 5xx, set `calix.economic_stale = true` (and/or `earnings_stale`) — the brief will surface a `CALIX_DEGRADED` flag rather than silently passing.

### 3. Fan out to news providers

The CLI fetches news itself if the bundle omits the `news` key — this lets the agent run the brief with one piping call. Provide API keys via env vars:

| Provider | Env var |
|---|---|
| Finnhub | `FINNHUB_API_KEY` |
| Marketaux | `MARKETAUX_API_KEY` |
| ForexNews | `FOREXNEWS_API_KEY` |

API keys never live in `config.toml`. A missing key for one provider doesn't block the others — the brief surfaces a `MISSING_NEWS_API_KEY` flag and per-provider health status so the user knows which sources contributed.

The CLI also auto-loads a `.env` file. Search order: `--env-file <path>` if passed → `~/.trading-agent-skills/.env` → `./.env` (CWD). Real shell env vars always win. `.env.example` at the repo root is the template; `.env` is gitignored. This is the recommended path on Windows / PowerShell, where bash `export` doesn't apply.

If you've already fetched the news upstream (e.g. you ran the providers in parallel via a custom script), pre-fill `news.articles_by_provider` + `news.provider_status` in the bundle to skip the in-CLI fan-out.

### 4. Build the bundle

```json
{
  "now_utc": "2026-04-29T21:00:00+00:00",
  "lookahead_hours": 4,
  "lookback_hours": 12,
  "explicit_watchlist": ["UKOIL"],
  "open_position_symbols": ["XAUUSD"],
  "calendar_event_currencies": ["USD", "EUR"],
  "earnings_constituent_indices": ["NAS100"],
  "volatility_ranked": ["XAGUSD", "BTCUSD"],
  "max_size": 8,
  "symbol_meta": {
    "UKOIL": {
      "currency_base": "UKOIL",
      "currency_profit": "USD",
      "category": "commodities",
      "swap_long": "125",
      "swap_short": "-150"
    }
  },
  "bars_by_symbol": {
    "UKOIL": [
      {"time": "...", "open": "...", "high": "...", "low": "...", "close": "...", "volume": 0}
    ]
  },
  "calix": {
    "economic_events": [<raw Calix /v1/calendar/economic shapes>],
    "earnings_entries": [<raw Calix /v1/calendar/earnings shapes>],
    "economic_stale": false,
    "earnings_stale": false
  }
}
```

### 5. Run the brief

```bash
echo '<bundle>' | python -m trading_agent_skills.cli.news
```

Or via entry point: `trading-agent-skills-news`.

### 6. Render

```json
{
  "generated_at_utc": "...",
  "watchlist": ["UKOIL", "XAUUSD", "NAS100", ...],
  "watchlist_description": "5/8 symbols: 1 from open positions, 2 from calendar, 2 from default",
  "watchlist_by_tier": {...},
  "calendar_by_symbol": {
    "UKOIL": [
      {"kind": "economic", "title": "EIA Crude Inventories", "when_utc": "...",
       "impact": "Medium", "detail": {"currency": "USD"}}
    ]
  },
  "news_by_symbol": {
    "UKOIL": [
      {"title": "OPEC+ holds production cuts",
       "publisher": "Reuters",
       "sources": ["finnhub/Reuters", "marketaux/Yahoo"],
       "url": "...",
       "published_at_utc": "...",
       "impact": "high",
       "summary": "Group leaves output unchanged."}
    ]
  },
  "swing_candidates": [
    {"symbol": "UKOIL", "direction": "long_carry",
     "rsi_14": "28.5", "swap_long": "125", "thesis": "..."}
  ],
  "health": {"finnhub": "ok", "marketaux": "no_api_key", "forexnews": "ok",
             "calix_economic": "ok", "calix_earnings": "ok"},
  "flags": ["MISSING_NEWS_API_KEY"],
  "notes": [...]
}
```

Render in this order so the user can scan top-down:

```
Morning brief — 5 symbols watched
  Watchlist: UKOIL, XAUUSD, NAS100, EURUSD, USOIL
  (1 from open positions, 2 from calendar, 2 from default)

⚡ Swing candidates (positive carry + technical extreme):
  UKOIL (D1 RSI 28, oversold; long pays $125/lot/night)
    Thesis: mean-reversion bounce on a positive-carry side.
            Verify Strait of Hormuz / OPEC+ headlines before entry.

📅 Calendar (next 4h):
  UKOIL — EIA Crude Inventories (Medium, 22:30 UTC)

📰 News (last 12h):
  UKOIL: OPEC+ holds production cuts — 3 sources [Reuters / Yahoo / Bloomberg]
         "Group leaves output unchanged."
  XAUUSD: FOMC holds rates — 2 sources [Reuters / Bloomberg]
          "Fed signals patience on cuts; dollar firms."

⚠ Health: Marketaux API key missing — Finnhub + ForexNews shown.
```

## Health and degraded modes

- `MISSING_NEWS_API_KEY` — at least one provider's env var is unset. Set the missing key(s) for fuller coverage.
- `NEWS_PROVIDER_DEGRADED` — at least one provider returned an HTTP error or timed out. Re-run after a few minutes; if persistent, that provider may have changed its API.
- `CALIX_DEGRADED` — Calix self-reported `stale: true` or returned a 5xx. The calendar-overlay section is from the prior cache; news-proximity in the checklist will WARN until it refreshes.
- `INDICATOR_DATA_INSUFFICIENT` — fewer than 21 D1 bars (or no symbol meta) for one or more watchlist symbols → that symbol is omitted from the swing-candidates evaluation. Pull longer history or remove from base universe.

## Common pitfalls

- **Currency vs ticker tagging.** Finnhub returns ticker symbols in `related`; Marketaux returns `entities` with symbols; ForexNews returns currency-pair codes (`["XAU-USD", "EUR-USD"]`). The orchestrator handles all three: explicit symbol → canonical-pair (`XAU-USD` ↔ `XAUUSD`) → index-constituent ticker (`AAPL` → `NAS100`) → bare-currency-tag intersection → article-keyword intersection → word-bounded topic vocab on title+summary (oil/crude/gold/silver). The substring-currency fallback was removed in 2026-05 because `"USD"` appears in nearly every forex headline and over-attributed every USD-quoted symbol with every USD-tagged article.
- **Index constituent expansion.** When an index (e.g. `NAS100`) is in the watchlist, the CLI fans out to Marketaux with the index's constituent equity tickers (AAPL, MSFT, GOOG, …) — the API doesn't recognise `NAS100` as an entity. The relevance matcher then routes those equity-tagged articles back to the index. Constituent maps live in `symbol_meta._INDEX_CONSTITUENTS`; expand them when adding indices to the watchlist.
- **Swap rates must come from the broker.** Don't paraphrase from news — ask `get_symbols` and pass the raw `swap_long` / `swap_short` Decimals. The "+$125/lot/night" claim in a UKOIL thesis is only credible if it matches the broker's actual swap rate at the time of the brief.
- **Indicator math needs at least 21 D1 bars** (RSI(14) needs 15, EMA(20) needs 20). Pull `get_rates(D1, 30)` for safety. Lower-timeframe ATR/RSI are not used here — the swing-candidates section is intentionally a daily-bar lens.
- **Dedup across providers** uses URL canonicalisation first (strips `utm_*`, `fbclid`, normalises host/scheme), then headline Levenshtein ratio (default 0.85). Same Reuters story syndicated by Yahoo collapses to one row with both sources listed.
- **Cache TTL is 60s** for both Calix and news fan-out. Re-running the brief within 60s returns cached data — fine for "show me again" follow-ups, suspicious if the user expects fresh post-FOMC data. Bypass by deleting `~/.trading-agent-skills/news_cache/` and `~/.trading-agent-skills/calix_cache/`.
- **No execution.** This skill never calls a mutating MCP tool. Surfacing a swing candidate is not a recommendation to enter — route through `pre-trade-checklist` for the discipline gates.
