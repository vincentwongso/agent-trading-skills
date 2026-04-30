# Trading Agent Skills — High-Level Plan

**Status:** Implementation-ready. Standalone document, intended to be moved into a new repository dedicated to Claude Code skills for day trading.

**Context:** The author runs Claude Code with the `mt5-mcp` MCP server (`C:\projects\mt5-trading-mcp`), which exposes 9 read tools and 4 mutating tools against a local MetaTrader 5 terminal. Two skills already exist in that repo: `mt5-market-data` (read-only quotes / positions / account / history) and `mt5-trading` (mutating place / modify / cancel / close with consent flow).

This plan extends that setup with **four reasoning-layer skills** that codify discipline, automate calculations, and integrate news + calendar context. None of them mutate broker state directly — they advise, gate, or record. Mutating actions still flow through `mt5-trading` with its existing consent flow.

External dependencies:
- **Calix** (`calix.fintrixmarkets.com`) — public HTTPS API for economic + earnings calendar (`C:\projects\calix`)
- **cfd-claculator** (`C:\projects\cfd-claculator`) — reference implementation for margin / pip / P&L / swap math; the canonical MT5 margin formula table lives at `docs/mt5/margin_requirements_formula.md`. Test fixtures from `tests/calculations/` will be ported to pytest.

---

## Prerequisites & shared infrastructure

Before building any skill, ensure the following are in place:

- **MCP servers configured:**
  - `mt5-mcp` (already running) — with the enhancements listed in the next section merged
- **External APIs:**
  - Calix at `https://calix.fintrixmarkets.com/v1/calendar/*` — public, CORS-gated, no auth, hourly-refreshed. Provides economic + earnings calendar.
  - News headlines (skill 4 only): `FINNHUB_API_KEY`, `MARKETAUX_API_KEY`, `FOREXNEWS_API_KEY` stored in OS keyring or env vars, never in code or committed config.
- **Reference repos:**
  - `C:\projects\mt5-trading-mcp` — MCP server source; PR target for enhancements.
  - `C:\projects\cfd-claculator` — margin formula reference + test fixtures.
  - `C:\projects\calix` — calendar API source; consumers do not need to read this, but it documents the response envelopes.
- **Skills directory layout** (target repo):
  ```
  .claude/skills/
    position-sizer/SKILL.md
    daily-risk-guardian/SKILL.md
    pre-trade-checklist/SKILL.md
    trade-journal/SKILL.md
    session-news-brief/SKILL.md
    _shared/
      calix_client.py        # Calix HTTPS wrapper, on-disk 60s cache
      news_clients.py        # fan-out + dedupe across Finnhub / Marketaux / ForexNews
      journal_io.py          # JSONL read/write helpers
      symbol_meta.py         # contract size, tick value, currency-pair parsing, currency-conversion
      margin_calc.py         # EnCalcMode dispatch (port of cfd-claculator/lib/calculations)
      risk_state.py          # is-position-risk-free predicate (LLM-driven, see Skill 2)
      indicators.py          # ATR, RSI from get_rates output
      config_io.py           # ~/.trading-agent-skills/config.toml read/write + first-run flow
      decimal_io.py          # Decimal helpers; mt5-mcp returns Decimal-as-string
  ```
- **Local data files** (created on first use, not committed):
  ```
  ~/.trading-agent-skills/
    journal.jsonl            # trade journal entries
    daily_state.json         # today's P&L window, last-reset timestamp (UTC ISO 8601)
    config.toml              # risk %, daily loss cap, watchlist, base universe, reset time
    news_cache/              # Calix + headline-API responses cached 60s
  ```

---

## mt5-mcp enhancements required

These ship as a single PR against `mt5-trading-mcp` before any skill work begins. All are pure adapter additions — no new mt5lib calls beyond what's listed.

### 1. Enrich `SymbolInfo`

Adapter source: `mt5lib.symbol_info(symbol)` already returns these fields; the adapter currently drops them.

| Field | Type | mt5lib source | Purpose |
|---|---|---|---|
| `calc_mode` | string enum | `trade_calc_mode` | Drives margin formula dispatch (forex, cfd, cfd_leverage, cfd_index, futures, exch_stocks, exch_bonds, exch_forts, forex_no_leverage, exch_options, exch_options_margin, serv_collateral) |
| `tick_value` | Decimal | `trade_tick_value` | Cash value of one tick in deposit currency |
| `swap_long` | Decimal | `swap_long` | Overnight financing rate, long side |
| `swap_short` | Decimal | `swap_short` | Overnight financing rate, short side |
| `swap_mode` | string enum | `swap_mode` | by_points, by_base_currency, by_interest_current, by_interest_open, by_margin_currency, by_profit_currency |
| `swap_rate_friday_3x` | bool | derived from `swap_rolloverday_3` | Triple-swap day flag |
| `margin_initial` | Decimal | `margin_initial` | Futures/exchange initial margin |
| `margin_maintenance` | Decimal | `margin_maintenance` | Futures/exchange maintenance margin |
| `margin_initial_buy` | Decimal | `margin_initial_buy` | Per-side initial margin rate |
| `margin_initial_sell` | Decimal | `margin_initial_sell` | Per-side initial margin rate |
| `margin_hedged` | Decimal | `margin_hedged` | Hedged-position margin |
| `stops_level` | int | `trade_stops_level` | Min distance (in points) for SL/TP |
| `freeze_level` | int | `trade_freeze_level` | Distance within which orders cannot be modified |

Naming: snake_case to match existing fields. Enums as readable strings, mapped in `adapter/conversions.py` (same convention used for `margin_mode`).

### 2. New tool: `get_rates`

```
get_rates(symbol: str, timeframe: str, count: int) -> list[Bar]
  timeframe: "M1" | "M5" | "M15" | "M30" | "H1" | "H4" | "D1" | "W1" | "MN1"
  count: int (max 5000)
  returns: [{time: datetime, open: Decimal, high: Decimal, low: Decimal,
             close: Decimal, tick_volume: int, real_volume: int, spread: int}, ...]
```

Backed by `mt5.copy_rates_from_pos(symbol, timeframe_const, 0, count)`. Read-only. Powers ATR / RSI / volatility ranking in skills 2 and 4.

### 3. New tool: `calc_margin`

```
calc_margin(symbol: str, side: "buy" | "sell", volume: Decimal, price: Decimal | None = None) -> CalcMarginResult
  returns: {margin: Decimal, currency: str}
```

Backed by `mt5.order_calc_margin(action, symbol, volume, price)`. If `price` is None, uses current ask (buy) / bid (sell). Returns broker-authoritative margin, used as cross-check against the formula-based calculation in `_shared/margin_calc.py`.

---

## Skill 1 — `position-sizer`

**Goal:** Compute correct lot size for a trade given account equity, risk %, stop distance, and symbol — accounting for contract size, leverage, margin consumption, and overnight swap exposure.

**Trigger phrases:**
- "size me into [symbol] with [N]% risk, stop at [price]"
- "what lot size for [symbol] short with stop [N] pips away"
- "how big should I trade [symbol] if I want to risk $X"

**Inputs:**
- Symbol (e.g. `EURUSD`, `XAUUSD`, `US500`)
- Risk amount: either % of equity OR absolute account-currency amount
- Stop distance: either price level OR pips/points
- Side (long / short)
- Optional: planned hold duration in nights (for swap-aware output)

**Outputs:**
- Recommended lot size (rounded to symbol's `volume_step`)
- Notional value of the trade
- **Margin required**, computed two ways:
  - Formula-based via `EnCalcMode` dispatch (per cfd-claculator's `margin_requirements_formula.md`)
  - Cross-checked against `calc_margin` (broker authoritative); flag if they disagree by >2%
  - Margin as % of free margin consumed
- **Swap-aware section:**
  - Swap rate per night per lot, both sides (`+$125/night long`, `-$48/night short` formatting)
  - Net swap if held N nights × proposed lot size (3× on Wednesdays for Friday rollover)
  - Note when swap-side aligns with trade-side (positive carry)
- Cash risk in account currency at the proposed stop, computed via `tick_value × ticks-to-stop × lots`
- **Sanity flags:**
  - Oversized (>30% margin consumed; threshold configurable)
  - Undersized (below `volume_min`)
  - Stop closer than `stops_level` × point (broker minimum)
  - Stop closer than current spread × 2 (likely to whipsaw)

**Dependencies:** `mt5-market-data` (`get_account_info`, `get_quote`, `get_symbols`, `calc_margin`). No external API.

**Behavior:**
1. Call `get_account_info` → equity, currency, leverage, free margin
2. Call `get_quote(symbol)` → current bid/ask
3. Call `get_symbols(category=...)` filtered to symbol → contract size, tick value, swap rates, calc_mode, stops_level, all margin fields
4. If quote currency ≠ account currency: call `get_quote(conversion_pair)` for FX conversion (e.g. account=USD, trading EURJPY → fetch USDJPY)
5. Compute cash-risk-per-lot using `tick_value` (now directly available, in account currency)
6. Lot size = (risk amount / cash-risk-per-lot), floored to `volume_step`, clamped to `[volume_min, volume_max]`
7. Compute margin via `_shared/margin_calc.py` dispatching on `calc_mode`; cross-check against `calc_margin` tool
8. Compute swap section using `swap_long`, `swap_short`, `swap_mode`; apply 3× multiplier if today is Wednesday and `swap_rate_friday_3x`
9. Surface all numbers in a structured response; flag anomalies; never auto-execute

**Risk tier:** Read-only / advisory.

**Acceptance criteria:**
- Returns same numbers as cfd-claculator for shared fixtures: EURUSD, XAUUSD, US500, USDJPY, EURJPY (cross with non-account profit currency)
- Formula-based margin matches `calc_margin` within 2% across all `calc_mode` variants the user actually trades (forex, cfd_leverage, cfd_index)
- Refuses to recommend a size below `volume_min` or above `volume_max` — reports the constraint instead
- Surfaces margin warning when consumed margin would exceed configured `margin_warning_pct` (default 30%)
- Stops_level violation surfaces a hard "stop too tight" warning and a recommended minimum distance
- Swap section omits gracefully when symbol has zero swap (e.g. some crypto symbols)
- Decimal arithmetic end-to-end; no float coercion

**Estimated effort:** 3–4 hours (slightly up from original — adding swap section + dual margin path).

---

## Skill 2 — `daily-risk-guardian` + `pre-trade-checklist` (paired build)

These two ship together because the checklist invokes the guardian as one of its checks.

### `daily-risk-guardian`

**Goal:** Track today's realized + unrealized P&L against a daily loss cap, and surface a CLEAR / CAUTION / HALT status. "Today" = since most recent NY 4pm ET close.

**Trigger phrases:**
- "what's my risk status today"
- "am I clear to take another trade"
- "how much have I lost today"

**Inputs:** None (or optionally a candidate trade size to assess incremental risk).

**Outputs:**
- Status: `CLEAR` / `CAUTION` / `HALT`
- Today's realized P&L (closed trades since last NY close)
- **At-risk position summary:**
  - Per-position: ticket, symbol, side, current SL relative to entry, classification (`AT_RISK` / `RISK_FREE` / `LOCKED_PROFIT`)
  - Risk classification is LLM-judged based on SL location, news, fundamentals, price action (see `_shared/risk_state.py` contract below)
  - Sum of per-position risk % for AT_RISK positions only — this is the **risk budget** consumed
- **Combined worst-case loss** vs. configured daily cap = realized today + sum of AT_RISK position drawdowns to SL
- Risk-budget utilization vs. cap: `2.3% of 5% daily cap` and `3.5% of 5% concurrent budget`
- Overnight financing flag for positions held >1 session (3× on Wednesdays per `swap_rate_friday_3x`)

**Risk-free predicate (LLM-judged).** Position is AT_RISK by default. Reclassify as RISK_FREE or LOCKED_PROFIT when the model can defend the call from available context:
- SL at or beyond entry (binary breakeven case) → RISK_FREE
- SL beyond entry by ≥ commission + spread on the closing leg → LOCKED_PROFIT
- Trailing stop with confluent technical level (e.g. SL just below a higher-low after consolidation) → may be RISK_FREE even if mathematically not yet breakeven, if the model judges the structure unlikely to break first
- Catalyst-pending position (e.g. UKOIL long with Strait of Hormuz tension intact, SL below recent swing) → AT_RISK by structure but model may surface "soft RISK_FREE pending event resolution"

The skill returns the classification *and* the model's reasoning. The user can override per-position via the journal or a `mark-risk-free <ticket>` config command. Default to AT_RISK if uncertain — false-positive RISK_FREE is the dangerous error.

**Dependencies:** `mt5-market-data` (`get_account_info`, `get_history`, `get_positions`, `get_quote`, `get_rates` for indicator context), local `daily_state.json`, `~/.trading-agent-skills/config.toml`.

**Behavior:**
1. Read config: `daily_loss_cap_pct` (default 5), `caution_threshold_pct_of_cap` (default 50), `concurrent_risk_budget_pct` (default 5), `reset_tz` (default `America/New_York`), `reset_time` (default `16:00`)
2. Determine today's session-open balance (read from `daily_state.json`; lazy reset on first call after computed UTC reset time, with DST handled via `zoneinfo.ZoneInfo`)
3. Call `get_account_info` → current equity
4. Call `get_history(today_open_utc, now_utc)` → closed deals for realized P&L
5. Call `get_positions` → unrealized P&L + each position's SL distance
6. For each position: classify via `_shared/risk_state.py` (model reasoning over SL+entry+context); compute drawdown to SL only for AT_RISK
7. Combined risk = realized today + Σ AT_RISK position drawdowns to SL
8. If combined ≥ cap → `HALT`; if ≥ caution threshold → `CAUTION`; else `CLEAR`
9. If Σ AT_RISK position risk % > `concurrent_risk_budget_pct` → also `CAUTION` (concurrent risk over budget regardless of realized P&L)
10. Flag any position open >1 session with computed swap accrual

**Risk tier:** Read-only / advisory.

### `pre-trade-checklist`

**Goal:** Run a structured gate before placing a new trade — verifies risk allowance, news proximity, session timing, existing exposure, and spread state — and outputs a pass/warn/block verdict.

**Trigger phrases:**
- "can I take a long on [symbol]"
- "check my rules for [symbol] entry"
- "pre-trade check for [symbol]"

**Inputs:**
- Symbol
- Side (long/short)
- Optional: proposed entry price, stop, lot size

**Outputs:**
- Verdict: `PASS` / `WARN` / `BLOCK`
- Per-check breakdown:
  - **Daily risk:** from `daily-risk-guardian`. HALT → BLOCK; CAUTION → WARN
  - **Concurrent budget:** would adding this trade push Σ AT_RISK risk % over `concurrent_risk_budget_pct`?
  - **News proximity:** Calix `GET /v1/calendar/economic/upcoming?currencies=<symbol_currencies>&impact=High&limit=10` filtered to next 4 hours. Within 30 min → WARN.
  - **Earnings proximity** (indices only): Calix `GET /v1/calendar/earnings/upcoming` filtered to constituents proxied by the index (NAS100 → AAPL/MSFT/NVDA/etc.). Same 30-min WARN window.
  - **Session timing:** `get_market_hours(symbol).is_open`. Closed → BLOCK.
  - **Exposure overlap:** existing positions in correlated symbols (USD-side overlap heuristic for v1; correlation matrix is a future enhancement)
  - **Spread state:** current spread vs. baseline (stored per symbol in `~/.trading-agent-skills/spread_baseline.json`, EWMA-updated on each call)

**Dependencies:** `daily-risk-guardian` (composed via shared module), Calix, `mt5-market-data`.

**Risk tier:** Read-only / advisory.

**Acceptance criteria:**
- `BLOCK` when daily loss cap is breached, regardless of other signals
- `BLOCK` when symbol is out of session
- `WARN` when high-impact Calix event is within 30 minutes for the symbol's currencies, OR when adding the trade would breach concurrent risk budget
- Verdict reasoning is human-readable, scannable in <5 seconds
- Skill does not call any mutating tool — output is informational only
- Calix degraded (returns `health: degraded`) → news check produces WARN with "calendar data stale" note rather than silently passing

**Estimated effort (combined):** 5–7 hours.

---

## Skill 3 — `trade-journal`

**Goal:** Record completed trades into a local JSONL file and generate periodic performance summaries by setup type and symbol.

**Trigger phrases:**
- "journal this trade: [details]"
- "log my last [symbol] trade"
- "show me my trade journal for [period]"
- "what's my win rate on [setup type]"

**Inputs (write):**
- Symbol, side, lot size, entry price, exit price, entry time, exit time
- **Original stop distance at entry** (required for R-multiple — never inferred after the fact)
- **Swap accrued** (separate field from realized P&L; pulled from closing deal's swap field via `get_history`)
- **Commission** (pulled from closing deal in `get_history` — open positions don't expose it)
- Setup type (free-form tag; skill suggests existing tags from prior entries to encourage consistency)
- Rationale (one paragraph, why entered)
- Risk classification at close (AT_RISK / RISK_FREE / LOCKED_PROFIT — captured from `risk_state.py` for analytics)
- Outcome notes (post-trade reflection, optional, may be added later via append-update)
- Optional: auto-populate entry/exit/PnL/swap/commission from `get_history` for a given ticket

**Inputs (read):**
- Period (today, this week, this month, all)
- Filter (symbol, setup type, direction, outcome, risk classification)

**Outputs (read):**
- Trade list with key fields
- Summary stats: trade count, win rate, avg R-multiple, expectancy, P&L total, **swap-only P&L (broken out)**
- Breakdown by setup type and symbol
- **Swing-trade lens:** subset of trades where `swap_accrued > |realized_pnl| × 0.2` — surfaces swap-harvest performance separately
- Flagged patterns: e.g. "you've taken 4 trades after 2pm and 3 lost"

**Schema (`schema_version: 1` from day one):**
```json
{
  "schema_version": 1,
  "uuid": "...",
  "type": "open" | "update",
  "ticket": 12345,
  "symbol": "UKOIL",
  "side": "buy",
  "volume": "1.0",
  "entry_price": "75.42",
  "exit_price": "78.10",
  "entry_time": "2026-04-29T07:30:00+00:00",
  "exit_time": "2026-05-02T15:45:00+00:00",
  "original_stop_distance_points": 80,
  "realized_pnl": "268.00",
  "swap_accrued": "375.00",
  "commission": "-7.50",
  "setup_type": "swap-harvest-long",
  "rationale": "Geopolitical tension intact, oversold on D1 RSI, positive swap...",
  "risk_classification_at_close": "LOCKED_PROFIT",
  "outcome_notes": null
}
```

`type: "update"` entries reference an earlier `uuid` and patch a subset of fields (e.g., adding `outcome_notes` retrospectively). Read-side resolves the latest version per uuid.

**Dependencies:** Local file `~/.trading-agent-skills/journal.jsonl`. `mt5-market-data` (`get_history` for auto-populate).

**Behavior:**
- **On write:** parse details, validate required fields, append a JSON line with generated UUID and ISO 8601 UTC timestamp
- **On read:** stream the JSONL, resolve update chains, filter by period/filters, compute summary stats in pure Python with Decimal
- **R-multiple computation:** `(realized_pnl + swap_accrued + commission) / (original_stop_distance_points × tick_value × volume)` — swap is part of trade outcome, not a side fund

**Risk tier:** Read-only (writes only to local file, never broker state).

**Acceptance criteria:**
- Survives Claude Code restart — journal persists; resilient to partial writes (line-buffered, fsync per entry)
- Read queries return correct stats for hand-curated test fixtures
- Setup-type tags are free-form but skill suggests existing tags (loaded into prompt) to encourage consistency
- Append-only with explicit `type: "update"` for retroactive edits — never silently overwrites
- `schema_version` validated on read; older versions trigger a documented migration path
- Optional: a `replay` command that walks the journal and reconstructs an equity curve

**Estimated effort:** 4–5 hours.

---

## Skill 4 — `session-news-brief`

**Goal:** Combine Calix calendar/earnings with a fan-out across 3 news providers, dedupe overlapping headlines, classify impact, and produce a session-start brief. Includes a swing-candidates section that surfaces positive-swap setups at technical extremes.

**Trigger phrases:**
- "news brief for the session"
- "what's happening on EURUSD"
- "any news on [symbol] in the last [N] hours"
- "morning brief"
- "any swing setups today"

**Inputs:**
- Optional: explicit watchlist
- Optional: lookback window (default 12 hours for morning brief, 1 hour for intra-session refresh)

**Watchlist resolution order** (deduped union, capped at 8 symbols):
1. Explicit input (if provided)
2. Currently open positions (via `get_positions`)
3. **Calendar-driven candidates:** map next-8h Calix economic events to liquid symbols by currency (USD events → XAUUSD, NAS100, US500, EURUSD, GBPUSD); next-8h Calix earnings → NAS100 / US500 if constituent earnings are flagged
4. **Volatility-ranked candidates:** ATR(14) on D1 from `get_rates`, normalized as percentage of price; top-N from configured `base_universe`
5. **Static fallback:** configured `default_watchlist` (initial: XAUUSD, XAGUSD, USOIL, UKOIL, NAS100) if everything above came back empty

`base_universe` and `default_watchlist` configurable in `config.toml`.

**Outputs:**
- **Calendar overlay** (Calix): high-impact economic + earnings events in next 4 hours, grouped by symbol
- **News digest** (3-API fan-out): high-impact stories per symbol, deduped, sentiment-tagged
  - For each story: source(s) (collapsed if same headline cross-published), headline + 1-line summary, sentiment, symbols affected, recency
- **Swing candidates section:**
  - For each watchlist symbol: D1 RSI, ATR-normalized distance from 20-EMA, current swap (long and short)
  - Surface symbols where: (RSI < 30 AND swap_long > 0) OR (RSI > 70 AND swap_short > 0)
  - For each candidate: directional thesis prompt ("UKOIL: D1 RSI 28, oversold; long swap +$125/lot/night; check geopolitical fundamentals before entry")
- **Health note:** if Calix returns `degraded` or any news API failed, surface explicitly ("Marketaux unavailable — Finnhub + ForexNews shown")

**Dependencies:**
- Calix HTTPS API (`_shared/calix_client.py`)
- Three news APIs via `_shared/news_clients.py`
  - Finnhub `/news` with category filters
  - Marketaux `/news/all` with `symbols=`, `entity_types=`
  - ForexNews API FX-specific feed
- `mt5-market-data`: `get_positions`, `get_rates` (for ATR/RSI), `get_symbols` (for swap rates), `get_quote` (for EMA distance)

**Behavior:**
1. Resolve watchlist per the order above
2. Map symbols to relevant news queries (`EURUSD` → news mentioning EUR, USD, ECB, Fed, US economic data)
3. Fire requests to all 3 news APIs + Calix in parallel via `asyncio.gather`; cache 60s on disk under `~/.trading-agent-skills/news_cache/`
4. Dedupe headlines by URL canonicalization + headline similarity (Levenshtein < 0.85 = duplicate)
5. Classify each story's impact:
   - High: central bank decision, jobs data, CPI, geopolitical event
   - Medium: earnings (for indices), commodity inventories
   - Low: opinion, analysis, broker color
6. Pull D1 bars via `get_rates` → compute ATR(14) and RSI(14) per watchlist symbol
7. Pull `swap_long` / `swap_short` from `get_symbols`
8. Compose swing-candidates section per the predicate above
9. Render structured response — symbol-grouped, recency-ordered, deduped

**Risk tier:** Read-only.

**Acceptance criteria:**
- Same story from Reuters → republished by Yahoo → republished by Marketaux collapses to one entry with 3 sources
- Symbol mapping correctly attributes Fed news to all USD pairs in the watchlist
- Skill is resilient to one API being down — degrades gracefully
- API keys live in env vars / keyring, never in skill code or committed config
- Swing-candidates section correctly identifies UKOIL-style setups (positive carry + technical extreme) on hand-curated fixtures
- Calix `degraded` health surfaces as a visible note, not a silent fallback

**Estimated effort:** 8–10 hours (up from original — adding ATR/RSI + swing section).

---

## First-run setup

On any skill's first invocation, if `~/.trading-agent-skills/config.toml` does not exist, the skill:

1. Detects missing config
2. Asks the user a one-shot setup question:
   > "First-run setup. I can configure with your stated defaults (1% per-trade max, 5% daily cap, 50% caution threshold, 5% concurrent risk budget, NY 4pm ET reset, base universe XAUUSD/XAGUSD/USOIL/UKOIL/NAS100), or walk you through each setting. Quick-start with defaults?"
3. On `y` / quick-start: writes the file with defaults below
4. On `n` / customize: walks each setting interactively, then writes

Subsequent updates via natural language: "set my daily cap to 4%" → skill reads + rewrites config.toml.

### Default `config.toml`

```toml
schema_version = 1

[risk]
per_trade_max_pct = 1.0
daily_loss_cap_pct = 5.0
caution_threshold_pct_of_cap = 50.0
concurrent_risk_budget_pct = 5.0
margin_warning_pct = 30.0

[session]
reset_tz = "America/New_York"
reset_time = "16:00"           # NY close = 6am AEST
display_tz = "Australia/Sydney"

[watchlist]
default = ["XAUUSD", "XAGUSD", "USOIL", "UKOIL", "NAS100"]
base_universe = ["XAUUSD", "XAGUSD", "USOIL", "UKOIL", "NAS100", "US500", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD"]
max_size = 8

[news]
dedup_similarity_threshold = 0.85
cache_seconds = 60

[indicators]
atr_period = 14
rsi_period = 14
rsi_oversold = 30
rsi_overbought = 70
```

API keys are NOT stored in `config.toml` — they live in env vars / keyring. The config carries a reference to which env var to read (e.g. `finnhub_key_env = "FINNHUB_API_KEY"`).

---

## Build sequence & dependency graph

```
mt5-mcp enhancements PR (SymbolInfo enrichment + get_rates + calc_margin)
              │
              ▼
position-sizer ──────── (independent, build first after mt5-mcp PR merges)
                                    │
                                    ▼
trade-journal ─────────── (independent; build early to start accumulating data)
                                    │
                                    ▼
daily-risk-guardian ──┐    (independent)
                      │
                      ▼
              pre-trade-checklist  (depends on guardian + Calix)
                      │
                      ▼
session-news-brief ────── (largest scope; uses everything)
```

**Recommended order:**
1. **mt5-mcp PR** — unblocks everything; adapter-only changes; <1 day
2. `position-sizer` — fastest to build, hits every trade, immediate ROI
3. `trade-journal` — start accumulating data ASAP
4. `daily-risk-guardian` + `pre-trade-checklist` (paired)
5. `session-news-brief` — largest, most integration

---

## Out of scope (explicitly NOT in this plan)

- **Automated trade execution.** All mutations stay behind the existing `mt5-trading` consent flow.
- **ML / predictive models.** These skills codify rules and aggregate data, they don't predict.
- **Backtesting infrastructure.** Use a dedicated tool (Backtrader, VectorBT) for backtesting; skills are for live decision support.
- **Multi-broker support.** MT5-only via `mt5-mcp`.
- **Mobile / web UI.** Skills are CLI-only via Claude Code.
- **Supplementary news sources** beyond Finnhub / Marketaux / ForexNews + Calix calendar.
- **Correlation matrix** for exposure overlap — v1 uses currency-side overlap heuristic only; matrix is a future enhancement.
- **Tick-level analytics.** Skills work with bars (`get_rates`), not tick streams.

---

## Remaining open questions (non-blocking)

1. **Headline-API rate budgets.** Each provider's per-minute limit needs to be documented in `_shared/news_clients.py` with exponential backoff. Will tune from real usage.
2. **Setup taxonomy growth.** Free-form with did-you-mean works for a single user; if the journal grows to >50 distinct tags, may need a manual consolidation pass.
3. **Calix watchlist for earnings constituents.** The mapping NAS100 → AAPL/MSFT/NVDA/etc. is editorial; will start with top-10 by weight and revisit if material earnings get missed.

---

## Suggested next step

Begin the **mt5-mcp enhancement PR** in `C:\projects\mt5-trading-mcp`:

1. Enrich `SymbolInfo` (13 new fields)
2. Add `get_rates` tool
3. Add `calc_margin` tool
4. Tests against fixtures lifted from cfd-claculator where applicable

Then proceed to `position-sizer` as the first skill build.
