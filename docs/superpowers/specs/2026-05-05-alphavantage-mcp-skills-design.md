# AlphaVantage MCP Skills — Design Spec

**Date:** 2026-05-05
**Status:** Approved design, pending implementation plan

## 1. Goal

Add AlphaVantage MCP as a data source to the trading-agent-skills stack. Five deliverables across four domains: economic indicators and alpha intelligence (sentiment + movers) integrate into the existing news brief; equity fundamentals, insider/institutional flow, and options pricing ship as standalone skills. The user is expanding into individual equities trading; options data is stored for future ML/quant consumption.

## 2. Architecture: Hybrid approach

**Integration skills** (compose with existing Python pipelines) get proper Python modules with Decimal typing, tests, and CLI entrypoints. **Standalone data skills** (raw data access) are SKILL.md-only — the agent calls AlphaVantage MCP directly and renders results. No Python CLI layer for standalone skills until a downstream consumer needs a specific shape.

Rationale: AlphaVantage MCP already handles auth, API calls, and returns structured JSON. Wrapping that in Python just to reshape it is premature for standalone data access. Integration points genuinely need Python because they feed into existing pure functions (`news_brief.py`, `news_dedup.py`).

## 3. Deliverables

| # | Name | Type | Pattern | Integrates with |
|---|------|------|---------|-----------------|
| 1 | Macro context section | Integration | Python module + SKILL.md change | `session-news-brief` |
| 2 | Sentiment enrichment + top movers | Integration | Python module + SKILL.md change | `session-news-brief` |
| 3 | Equity fundamentals | Standalone | SKILL.md-only | New skill |
| 4 | Insider & institutional | Standalone | SKILL.md-only | New skill |
| 5 | Options data | Standalone | SKILL.md-only | New skill |

**AlphaVantage MCP tool mapping:**

| Deliverable | AV MCP tools consumed |
|---|---|
| Macro context | `REAL_GDP`, `CPI`, `INFLATION`, `TREASURY_YIELD`, `FEDERAL_FUNDS_RATE`, `UNEMPLOYMENT`, `NONFARM_PAYROLL`, `RETAIL_SALES`, `DURABLES` |
| Sentiment + movers | `NEWS_SENTIMENT`, `TOP_GAINERS_LOSERS` |
| Fundamentals | `COMPANY_OVERVIEW`, `INCOME_STATEMENT`, `BALANCE_SHEET`, `CASH_FLOW`, `EARNINGS` |
| Insider & institutional | `INSIDER_TRANSACTIONS`, `INSTITUTIONAL_HOLDINGS` |
| Options data | `REALTIME_OPTIONS`, `REALTIME_OPTIONS_FMV`, `HISTORICAL_OPTIONS` |

**API key:** AlphaVantage MCP handles auth internally via its server configuration. No new env vars needed in this repo.

## 4. Deliverable 1 — Macro Context section in `session-news-brief`

### AV MCP tools called by the agent (parallel fan-out)

- `TREASURY_YIELD` (interval=daily, maturity=10year)
- `FEDERAL_FUNDS_RATE` (interval=daily)
- `CPI` (interval=monthly)
- `INFLATION` (interval=annual)
- `UNEMPLOYMENT` (interval=monthly)
- `NONFARM_PAYROLL` (interval=monthly)
- `REAL_GDP` (interval=quarterly)
- `RETAIL_SALES` (interval=monthly)
- `DURABLES` (interval=monthly)

### New Python module: `src/trading_agent_skills/macro_context.py`

Pure function, same pattern as all existing modules:

```python
def build_macro_context(indicators: dict[str, list[dict]]) -> MacroContext:
    """Takes raw AV indicator responses keyed by name,
    extracts latest + previous reading, computes delta/direction."""
```

**Input:** dict of indicator name to list of `{"date": "...", "value": "..."}` entries (common AV economic indicator response shape). All values arrive as strings, coerced to Decimal via `D()`.

**Output:** `MacroContext` dataclass:
- `readings`: list of `MacroReading(name, latest_value, latest_date, previous_value, previous_date, direction)` where direction is `"rising"` / `"falling"` / `"flat"`
- `staleness_flags`: list of indicator names where latest reading is older than expected cadence (e.g., GDP older than 3 months)

### Integration point

`news_brief.py`'s `build_brief()` gets a new optional `macro_context: MacroContext | None` parameter. When present, the output dict includes a `"macro_context"` section. When absent (AV MCP not configured), the brief runs unchanged.

### SKILL.md change

`session-news-brief/SKILL.md` gets a new step between "Gather symbol metadata" and "Build the bundle": fan out AV macro calls, include results in the bundle JSON under `"macro_indicators"` key.

### Caching

Economic data changes infrequently (daily at most for yields, monthly/quarterly for others). No additional Python-side caching needed beyond what the MCP server provides.

## 5. Deliverable 2 — Sentiment enrichment + Top Movers in `session-news-brief`

### 5a: NEWS_SENTIMENT enrichment

**Problem:** Current news pipeline classifies impact via keyword matching (`news_dedup.classify_impact`). AV's NEWS_SENTIMENT returns per-ticker NLP scores: `relevance_score` (0-1), `ticker_sentiment_score` (-1 to +1), `ticker_sentiment_label` (Bullish/Bearish/Neutral).

**Agent calls:** For each symbol on the resolved watchlist, call `NEWS_SENTIMENT` with `tickers=<symbol>` and `time_from=<lookback>`. Runs in parallel with the existing Finnhub/Marketaux/ForexNews fan-out.

**New Python module: `src/trading_agent_skills/av_sentiment.py`**

```python
def enrich_articles_with_sentiment(
    articles: list[NewsArticle],
    av_sentiment: list[dict],
) -> list[NewsArticle]:
    """Match AV sentiment entries to existing articles by URL/title similarity,
    attach sentiment scores. Unmatched AV entries become new NewsArticle items."""
```

- Matching uses existing `canonicalise_url` + Levenshtein dedup from `news_dedup.py` to pair AV sentiment entries with articles already gathered from Finnhub/Marketaux/ForexNews.
- Matched articles get `sentiment_score`, `sentiment_label`, `relevance_score` fields populated.
- Unmatched AV entries (articles the other 3 providers missed) are converted to `NewsArticle` and added — AV becomes effectively a 4th news source.
- Existing keyword-based `classify_impact` remains as fallback when AV sentiment is unavailable.

### 5b: TOP_GAINERS_LOSERS section

**Agent calls:** Single call to `TOP_GAINERS_LOSERS` (no parameters). Returns three arrays: `top_gainers`, `top_losers`, `most_actively_traded` — each entry has `ticker`, `price`, `change_amount`, `change_percentage`, `volume`.

**No new Python module.** Simple structured data — `build_brief()` gets a new optional `top_movers: dict | None` parameter and passes it through to the output dict under `"top_movers"`.

### Integration point

`news_brief.py`'s `build_brief()` gets two new optional parameters:
- `av_sentiment: list[dict] | None` — when present, calls `enrich_articles_with_sentiment` before dedup/ranking
- `top_movers: dict | None` — when present, included in output dict

### SKILL.md change

`session-news-brief/SKILL.md` adds:
1. `NEWS_SENTIMENT` calls per watchlist symbol (parallel with existing news fan-out)
2. Single `TOP_GAINERS_LOSERS` call (parallel with everything else)
3. Both results included in the bundle JSON under `"av_sentiment"` and `"top_movers"` keys

### Degradation

If AlphaVantage MCP isn't configured or calls fail, both features degrade gracefully — existing 3-API news pipeline runs unchanged, no top movers section. An `"av_status": "unavailable"` flag surfaces in the brief's health section.

## 6. Deliverable 3 — Equity Fundamentals (standalone)

**New skill: `.claude/skills/equity-fundamentals/SKILL.md`**

SKILL.md-only, no Python CLI layer.

### Trigger phrases

- "fundamentals for AAPL"
- "show me TSLA's balance sheet"
- "what's NVDA's P/E ratio"
- "income statement for MSFT"
- "how's AMZN doing financially"

### AV MCP tools (agent fans out in parallel)

- `COMPANY_OVERVIEW(symbol)` — P/E, EPS, market cap, sector, description, 52-week range, dividend yield, beta
- `INCOME_STATEMENT(symbol)` — annual + quarterly revenue, net income, EBITDA, operating margin
- `BALANCE_SHEET(symbol)` — total assets, liabilities, equity, cash, debt
- `CASH_FLOW(symbol)` — operating/investing/financing cash flow, free cash flow, capex
- `EARNINGS(symbol)` — actual vs. estimated EPS, surprise percentage

### Workflow

1. User asks about a symbol. If ambiguous, agent uses `SYMBOL_SEARCH` to resolve.
2. Fan out all 5 MCP calls in parallel.
3. Agent renders structured summary:
   - **Overview** — sector, market cap, P/E, EPS, dividend yield, beta, 52-week range
   - **Income** — latest annual + most recent quarter, revenue/net income trend (last 4 quarters)
   - **Balance sheet** — debt-to-equity, current ratio, cash position
   - **Cash flow** — free cash flow, operating cash flow margin
   - **Earnings** — last 4 quarters actual vs. estimate, surprise %
4. Agent highlights notable items (e.g., "earnings beat 3 of last 4 quarters", "debt-to-equity above 2.0").

### Multi-symbol

"Compare AAPL and MSFT fundamentals" — agent fans out both in parallel, renders side-by-side.

## 7. Deliverable 4 — Insider & Institutional (standalone)

**New skill: `.claude/skills/insider-institutional/SKILL.md`**

SKILL.md-only.

### Trigger phrases

- "any insider activity on AAPL"
- "who's buying TSLA shares"
- "institutional holders of NVDA"
- "insider transactions for MSFT this quarter"
- "show me smart money on AMZN"

### AV MCP tools (parallel)

- `INSIDER_TRANSACTIONS(symbol)` — transaction date, insider name, title, transaction type (buy/sell/exercise), shares traded, value, shares owned after
- `INSTITUTIONAL_HOLDINGS(symbol)` — institution name, shares held, market value, portfolio weight, change in shares quarter-over-quarter

### Workflow

1. Resolve symbol (use `SYMBOL_SEARCH` if ambiguous).
2. Fan out both MCP calls in parallel.
3. Agent renders:
   - **Insider activity** — recent transactions sorted by date, flagging notable patterns (e.g., "CEO bought $2M in last 30 days", "cluster of insider sells this month")
   - **Top institutional holders** — top 10 by position size, highlighting quarter-over-quarter changes (new position, increased, decreased, exited)
   - **Signal summary** — one-line read: "Net insider buying + institutional accumulation" or "Mixed: insider sells offset by new institutional positions"

### Multi-symbol

"Compare insider activity AAPL vs MSFT" — parallel fan-out, side-by-side render.

### Relationship to fundamentals

Separate skill (financial health vs. smart money flow), but the agent can invoke both for broad requests like "full picture on AAPL".

## 8. Deliverable 5 — Options Data (standalone)

**New skill: `.claude/skills/options-data/SKILL.md`**

SKILL.md-only. Raw data access layer for future ML/quant consumption.

### Trigger phrases

- "options chain for SPY"
- "show me AAPL options"
- "what's the options pricing on TSLA"
- "historical options for NVDA on 2026-04-30"
- "fair value on SPY options"
- "IV on AAPL calls"

### AV MCP tools

- `REALTIME_OPTIONS(symbol, contract?)` — current chain with Greeks (delta, gamma, theta, vega, rho), bid/ask, volume, open interest, IV per contract
- `REALTIME_OPTIONS_FMV(symbol)` — fair market value mark prices (mid-market theoretical value)
- `HISTORICAL_OPTIONS(symbol, date?)` — historical chain snapshots

### Workflow

1. Resolve symbol.
2. Determine which tools based on request:
   - "options chain" / "options on X" -> `REALTIME_OPTIONS` + `REALTIME_OPTIONS_FMV` in parallel
   - "historical options" / "options on X on [date]" -> `HISTORICAL_OPTIONS(symbol, date)`
   - "full options picture" -> all three in parallel
3. Agent renders:
   - **Chain summary** — calls and puts for nearest 3 expirations: strike, bid/ask, IV, delta, OI, volume
   - **Notable strikes** — highest open interest, highest volume (unusual activity), extreme IV skew
   - **FMV comparison** — where mark prices diverge significantly from mid-market (mispricing signal)
   - For historical: same structure for the requested date

### Raw data mode

User can request "raw options data for AAPL" — agent outputs full JSON response minimally formatted, for piping into notebooks, scripts, or future ML pipelines.

## 9. Testing strategy

### Integration skills (deliverables 1-2)

Full pytest coverage, same pattern as existing modules:
- `tests/test_macro_context.py` — hand-rolled fixture factories mimicking AV response shapes, test direction computation, staleness detection, Decimal coercion
- `tests/test_av_sentiment.py` — test article matching (URL match, title fuzzy match, no match), sentiment field population, graceful handling of empty/malformed AV responses
- `tests/test_news_brief_av_integration.py` — test that `build_brief()` includes macro context and top movers when provided, omits them when not

### Standalone skills (deliverables 3-5)

No Python tests needed — there's no Python code. The SKILL.md files are validated by manual invocation against the live AlphaVantage MCP server.

## 10. File inventory

### New files

| File | Purpose |
|---|---|
| `src/trading_agent_skills/macro_context.py` | Macro context builder (Deliverable 1) |
| `src/trading_agent_skills/av_sentiment.py` | AV sentiment enrichment (Deliverable 2) |
| `.claude/skills/equity-fundamentals/SKILL.md` | Equity fundamentals skill (Deliverable 3) |
| `.claude/skills/insider-institutional/SKILL.md` | Insider & institutional skill (Deliverable 4) |
| `.claude/skills/options-data/SKILL.md` | Options data skill (Deliverable 5) |
| `tests/test_macro_context.py` | Macro context tests |
| `tests/test_av_sentiment.py` | Sentiment enrichment tests |

### Modified files

| File | Change |
|---|---|
| `src/trading_agent_skills/news_brief.py` | Add `macro_context`, `av_sentiment`, `top_movers` optional params to `build_brief()` |
| `src/trading_agent_skills/news_dedup.py` | Add optional `sentiment_score`, `sentiment_label`, `relevance_score` fields to `NewsArticle` |
| `.claude/skills/session-news-brief/SKILL.md` | Add AV macro + sentiment + top movers fan-out steps |
| `src/trading_agent_skills/cli/news.py` | Accept new bundle keys, pass to `build_brief()` |

## 11. Conventions preserved

- Decimal-typed money/price everywhere in Python modules; reject floats at boundaries via `D()`
- JSON-stdin -> pure function -> JSON-stdout for CLI surfaces
- Hand-rolled fixture factories over mocks
- Strict validation at write boundaries
- Conventional commit messages, no Co-Authored-By trailer
- Graceful degradation when AlphaVantage MCP is unavailable
