---
name: options-data
description: Use when the user asks for options chains, options pricing, implied volatility, Greeks, or fair market value on a symbol (current or historical). Read-only.
---

# Options Data

Raw data access layer for US equity options via AlphaVantage MCP. Exposes realtime chains with Greeks, fair market value marks, and historical snapshots. Designed for inspection now and ML/quant pipeline consumption later.

This skill never executes trades — output is informational.

## Prerequisites

1. **AlphaVantage MCP server is configured.** Verify with `mcp__alphavantage__PING`. If not configured, tell the user: "The options-data skill requires the AlphaVantage MCP server. Add it to your MCP configuration with your API key."

## When to invoke

- "options chain for SPY" / "show me AAPL options"
- "what's the options pricing on TSLA"
- "historical options for NVDA on 2026-04-30"
- "fair value on SPY options"
- "IV on AAPL calls"
- "raw options data for SPY"
- "put/call ratio for QQQ"

Don't invoke for: stock price/quote (use `mt5-market-data`), company financials (use `equity-fundamentals`), insider activity (use `insider-institutional`).

## Inputs

1. **Symbol** — required.
2. **Date** — optional, for historical queries only (ISO format).
3. **Contract** — optional, for specific contract lookup.

## Workflow

### 1. Resolve symbol

Same as other skills — use `mcp__alphavantage__SYMBOL_SEARCH` if ambiguous.

### 2. Determine which tools to call

| User request | Tools |
|---|---|
| "options chain" / "options on X" | `REALTIME_OPTIONS` + `REALTIME_OPTIONS_FMV` (parallel) |
| "historical options on [date]" | `HISTORICAL_OPTIONS(symbol, date)` |
| "full options picture" | All three (parallel) |
| "raw options data" | `REALTIME_OPTIONS` (full output, minimal formatting) |
| Specific contract | `REALTIME_OPTIONS(symbol, contract=<id>)` |

### 3. Fan out MCP calls

- `mcp__alphavantage__REALTIME_OPTIONS(symbol=<sym>)` — current chain with Greeks
- `mcp__alphavantage__REALTIME_OPTIONS_FMV(symbol=<sym>)` — fair market value marks
- `mcp__alphavantage__HISTORICAL_OPTIONS(symbol=<sym>, date=<date>)` — historical snapshot

### 4. Render (standard mode)

Show calls and puts for the nearest 3 expirations:

```
SPY Options Chain — as of 2026-05-05 15:45 ET

Expiry: 2026-05-09 (4 DTE)
Strike  | Type | Bid    | Ask    | IV     | Delta  | OI      | Volume
--------|------|--------|--------|--------|--------|---------|-------
520     | Call | 3.45   | 3.52   | 18.2%  | 0.62   | 45,230  | 12,500
520     | Put  | 2.10   | 2.18   | 17.8%  | -0.38  | 38,100  | 9,800
525     | Call | 1.22   | 1.28   | 19.1%  | 0.38   | 52,400  | 15,200
525     | Put  | 4.85   | 4.93   | 18.5%  | -0.62  | 29,600  | 7,300

Notable Strikes:
  Highest OI: 525 Call (52,400) — potential resistance
  Highest Volume: 525 Call (15,200) — unusual activity today
  IV Skew: Puts trading 0.4% higher IV than calls at 520 strike

FMV Comparison (where mark differs from mid by >5%):
  525 Put:  Mid $4.890, FMV $5.05 (+3.3%) — slight underpricing
```

### 5. Raw data mode

When the user requests "raw options data", output the full JSON response with minimal formatting for piping into notebooks, scripts, or future ML pipelines.

### 6. Historical mode

For historical queries, show the same table structure but note the date and that these are end-of-day values.
