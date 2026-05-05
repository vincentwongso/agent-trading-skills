---
name: equity-fundamentals
description: Use when the user asks for equity fundamental analysis, company financials, P/E ratio, earnings, balance sheet, income statement, or cash flow for a stock ticker. Triggers on phrases like "fundamentals for AAPL", "show me TSLA's balance sheet", "what's NVDA's P/E ratio", "income statement for MSFT", "how's AMZN doing financially", "compare AAPL and MSFT fundamentals". Calls AlphaVantage MCP tools directly — no Python CLI layer. Read-only / advisory — never executes trades.
---

# Equity Fundamentals

Fetches and renders company fundamentals from AlphaVantage MCP: overview, income statement, balance sheet, cash flow, and earnings data. Covers US-listed equities and ETFs.

This skill never executes trades — output is informational. Use alongside [`insider-institutional`](../insider-institutional/SKILL.md) for smart-money context, or [`price-action`](../price-action/SKILL.md) for technical structure.

## Prerequisites

1. **AlphaVantage MCP server is configured.** Verify with `mcp__alphavantage__PING`. If not configured, tell the user: "The equity-fundamentals skill requires the AlphaVantage MCP server. Add it to your MCP configuration with your API key."

## When to invoke

- "fundamentals for AAPL" / "financials for TSLA"
- "show me NVDA's balance sheet"
- "what's MSFT's P/E ratio" / "earnings for AMZN"
- "income statement for GOOG"
- "how's META doing financially"
- "compare AAPL and MSFT fundamentals"

Don't invoke for: price/quote data (use `mt5-market-data`), news/sentiment (use `session-news-brief`), insider/institutional activity (use `insider-institutional`), options pricing (use `options-data`).

## Inputs

1. **Symbol(s)** — required. One or more equity tickers. If ambiguous, resolve with `mcp__alphavantage__SYMBOL_SEARCH(keywords=<query>)`.

## Workflow

### 1. Resolve symbol

If the user says a company name instead of a ticker (e.g. "Apple"), call:
- `mcp__alphavantage__SYMBOL_SEARCH(keywords="Apple")`
Pick the best match and confirm with the user if ambiguous.

### 2. Fan out MCP calls (parallel)

For each symbol, call all 5 in parallel:
- `mcp__alphavantage__COMPANY_OVERVIEW(symbol=<sym>)`
- `mcp__alphavantage__INCOME_STATEMENT(symbol=<sym>)`
- `mcp__alphavantage__BALANCE_SHEET(symbol=<sym>)`
- `mcp__alphavantage__CASH_FLOW(symbol=<sym>)`
- `mcp__alphavantage__EARNINGS(symbol=<sym>)`

### 3. Render

Structure the output as:

```
AAPL — Apple Inc. (Technology / Consumer Electronics)

Overview:
  Market Cap: $3.1T | P/E: 32.5 | EPS: $6.42
  Dividend Yield: 0.52% | Beta: 1.24
  52-Week: $164.08 - $199.62

Income (Latest Quarter):
  Revenue: $124.3B (+8.2% YoY) | Net Income: $36.3B
  Operating Margin: 33.2% | EBITDA: $45.1B
  Trend (last 4Q): $110.5B -> $85.8B -> $94.9B -> $124.3B

Balance Sheet:
  Total Assets: $352.6B | Total Liabilities: $290.4B
  Debt-to-Equity: 4.67 | Current Ratio: 0.99
  Cash & Equivalents: $29.9B

Cash Flow:
  Operating CF: $39.9B | Free CF: $28.5B
  CapEx: $11.4B | FCF Margin: 22.9%

Earnings (Last 4 Quarters):
  Q1 2026: $2.10 actual vs $2.05 est (+2.4% surprise)
  Q4 2025: $1.64 actual vs $1.60 est (+2.5% surprise)
  Q3 2025: $1.46 actual vs $1.35 est (+8.1% surprise)
  Q2 2025: $1.40 actual vs $1.34 est (+4.5% surprise)

Notable: Earnings beat 4 of last 4 quarters. Debt-to-equity 4.67 is
elevated but typical for AAPL's buyback-funded capital structure.
```

### 4. Multi-symbol comparison

For "compare AAPL and MSFT", fan out all 10 calls (5 per symbol) in parallel and render side-by-side.

### 5. Highlight notable items

Flag any of:
- Earnings beat/miss streak (3+ consecutive)
- Debt-to-equity above 2.0
- Negative free cash flow
- Revenue declining quarter-over-quarter
- P/E significantly above/below sector average (if sector data available)
