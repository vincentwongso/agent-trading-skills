---
name: insider-institutional
description: Use when the user asks about insider transactions, institutional holdings, smart money flow, or who is buying/selling a stock. Triggers on phrases like "any insider activity on AAPL", "who's buying TSLA shares", "institutional holders of NVDA", "insider transactions for MSFT this quarter", "show me smart money on AMZN". Calls AlphaVantage MCP tools directly — no Python CLI layer. Read-only / advisory — never executes trades.
---

# Insider & Institutional

Fetches insider transactions and institutional holdings from AlphaVantage MCP. Shows who is buying/selling inside the company and which institutions are accumulating or reducing positions.

This skill never executes trades — output is informational. Use alongside [`equity-fundamentals`](../equity-fundamentals/SKILL.md) for financial health context.

## Prerequisites

1. **AlphaVantage MCP server is configured.** Verify with `mcp__alphavantage__PING`. If not configured, tell the user: "The insider-institutional skill requires the AlphaVantage MCP server. Add it to your MCP configuration with your API key."

## When to invoke

- "any insider activity on AAPL"
- "who's buying TSLA shares" / "insider transactions for MSFT"
- "institutional holders of NVDA" / "who holds GOOG"
- "show me smart money on AMZN"
- "compare insider activity AAPL vs MSFT"

Don't invoke for: company financials (use `equity-fundamentals`), price data (use `mt5-market-data`), options (use `options-data`).

## Inputs

1. **Symbol(s)** — required. If ambiguous, resolve with `mcp__alphavantage__SYMBOL_SEARCH(keywords=<query>)`.

## Workflow

### 1. Resolve symbol

Same as equity-fundamentals — use `SYMBOL_SEARCH` if the user gives a company name.

### 2. Fan out MCP calls (parallel)

For each symbol:
- `mcp__alphavantage__INSIDER_TRANSACTIONS(symbol=<sym>)`
- `mcp__alphavantage__INSTITUTIONAL_HOLDINGS(symbol=<sym>)`

### 3. Render

```
AAPL — Insider & Institutional Activity

Insider Transactions (Last 90 Days):
  2026-04-15  Tim Cook (CEO)         SELL   50,000 shares  $9.76M  [now holds 3.28M]
  2026-04-01  Luca Maestri (CFO)     SELL   25,000 shares  $4.87M  [now holds 1.12M]
  2026-03-20  Jeff Williams (COO)    BUY    10,000 shares  $1.93M  [now holds 489K]

  Pattern: Mixed — CEO/CFO selling (routine 10b5-1), COO buying.

Top Institutional Holders:
  #  Institution              Shares      Value       Weight  QoQ Change
  1  Vanguard Group           1.28B      $248.6B     8.1%    +0.3%
  2  BlackRock                1.02B      $198.1B     6.5%    +0.1%
  3  Berkshire Hathaway       915M       $177.6B     5.8%    -2.1%
  4  State Street             603M       $117.0B     3.8%    +0.5%
  5  FMR LLC                  350M       $67.9B      2.2%    +1.2%

Signal: Net insider selling (mostly routine/scheduled). Institutional
accumulation by Vanguard/State Street/FMR; Berkshire trimming 2.1%.
```

### 4. Multi-symbol comparison

For "compare insider activity AAPL vs MSFT", fan out all 4 calls (2 per symbol) in parallel. Render each symbol, then a comparison summary.

### 5. Flag notable patterns

- Cluster of insider buys (3+ insiders buying within 30 days)
- Large insider buy by CEO/CFO (> $1M)
- Insider sells at 52-week high
- New institutional position (0 to significant holding)
- Major institution exiting entirely
