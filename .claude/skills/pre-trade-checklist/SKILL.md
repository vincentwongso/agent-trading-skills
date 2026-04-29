---
name: pre-trade-checklist
description: Use when the user asks whether they can take a specific trade, wants a structured pre-entry sanity check, or wants to verify that a candidate position passes their rules. Triggers on phrases like "can I take a long on [symbol]", "check my rules for [symbol] entry", "pre-trade check for [symbol]", "is it safe to enter [symbol] now", "should I take [symbol] [long/short] here". Composes daily-risk-guardian with Calix calendar proximity, session timing, exposure overlap, and spread-baseline checks; returns PASS / WARN / BLOCK + per-check breakdown. Read-only / advisory — never executes.
---

# Pre-trade Checklist

Runs a structured gate before a new entry: composes [`daily-risk-guardian`](../daily-risk-guardian/SKILL.md), checks Calix economic + earnings proximity, session timing, exposure overlap, and spread-vs-baseline. Aggregates a single `PASS / WARN / BLOCK` verdict from per-check sub-statuses.

The skill never executes orders — output is informational. Use the [`mt5-trading`](../mt5-trading/SKILL.md) skill (or its equivalent) for actual entries.

## When to invoke

Trigger phrases:
- "can I take a long on [symbol]"
- "pre-trade check for [symbol]"
- "check my rules for [symbol] entry"
- "is it safe to enter [symbol] now"
- "should I take [symbol] short here"

Don't invoke for general market-data questions (`mt5-market-data`) or for sizing questions (use [`cfd-position-sizer`](../cfd-position-sizer/SKILL.md) first to get a candidate `risk_pct`, then this skill to gate the entry).

## Inputs

Required from the user:
- **Symbol** (broker symbol — e.g. `XAUUSD`, `NAS100`)
- **Side** (`long` / `short`)

Optional:
- **Candidate risk % of equity** — what the position-sizer recommended (drives the concurrent-budget projection)

## Workflow

### 1. Gather guardian inputs

Same as `daily-risk-guardian` (see that SKILL.md): `get_account_info`, `get_positions` (+ `get_symbols` for tick_size/value), `get_history` for today's realized P&L. Classify each open position AT_RISK / RISK_FREE / LOCKED_PROFIT.

### 2. Gather symbol context

- `mcp__mt5-mcp__get_symbols(category=...)` filtered to the **target** symbol → currency_base, currency_profit, category.
- `mcp__mt5-mcp__get_market_hours(target_symbol)` → `is_open` flag.
- `mcp__mt5-mcp__get_quote(target_symbol)` → bid + ask. Compute current spread in points: `(ask - bid) / tick_size`.

### 3. Gather Calix calendar data

```bash
# Economic — filter to currencies of interest for the symbol
curl -s "https://calix.fintrixmarkets.com/v1/calendar/economic/upcoming?currencies=USD,EUR&impact=High&limit=10"

# Earnings — only relevant for indices
curl -s "https://calix.fintrixmarkets.com/v1/calendar/earnings/upcoming?limit=20"
```

Use `currencies_of_interest` mapping (mt5 symbol_meta module): FX → both sides; metals → quote currency; indices → mapped country/region currency (NAS100 → USD, GER40 → EUR, UKOIL → USD+GBP). Bundle the responses raw — the Python checklist parses both events list and the `stale` boolean.

If Calix is unreachable or returns 5xx, treat as `economic_stale: true` (the checklist will WARN with "calendar data stale" — better safe than silently passing).

### 4. Build the bundle

```json
{
  "now_utc": "2026-04-29T21:00:00+00:00",
  "account": { /* get_account_info */ },
  "positions": [ /* same shape as guardian */ ],
  "realized_pnl_today": "150.00",
  "target": {
    "symbol": "XAUUSD",
    "side": "long",
    "candidate_risk_pct": "1.0"
  },
  "symbol_context": {
    "currency_base": "XAU",
    "currency_profit": "USD",
    "category": "metals",
    "market_open": true
  },
  "calix": {
    "economic_events": [ /* raw Calix events array */ ],
    "earnings_entries": [ /* raw Calix earnings array */ ],
    "economic_stale": false,
    "earnings_stale": false
  },
  "spread": { "current_pts": "12" }
}
```

### 5. Run the checklist

```bash
echo '<bundle>' | python -m cfd_skills.cli.checklist
```

Or via entry point: `cfd-skills-checklist`.

### 6. Render the result

```json
{
  "verdict": "PASS | WARN | BLOCK",
  "symbol": "XAUUSD",
  "side": "long",
  "checks": [
    {"name": "daily_risk", "status": "PASS", "reason": "...", "detail": {...}},
    {"name": "concurrent_budget", "status": "PASS", "reason": "...", "detail": {...}},
    {"name": "session", "status": "PASS", "reason": "...", "detail": {...}},
    {"name": "news_proximity", "status": "WARN", "reason": "...", "detail": {...}},
    {"name": "earnings_proximity", "status": "PASS", "reason": "...", "detail": {...}},
    {"name": "exposure_overlap", "status": "PASS", "reason": "...", "detail": {...}},
    {"name": "spread", "status": "PASS", "reason": "...", "detail": {...}}
  ],
  "flags": [...],
  "notes": [...],
  "guardian": { /* full guardian output for context rendering */ },
  "session_just_reset": false
}
```

Render so the user can scan in <5 seconds:

```
WARN — XAUUSD long
  ✓ Daily risk: CLEAR (worst case 1.5% / 5% cap)
  ✓ Concurrent budget: 4% projected / 5% budget
  ✓ Session: open
  ⚠ News: FOMC Statement in 22 min (USD)
  ✓ Earnings: not applicable
  ✓ Exposure: no overlapping positions
  ✓ Spread: 12 pts within 2x baseline (10.5)
Verdict: WARN — proceed with reduced size or wait through the FOMC release.
```

When verdict is BLOCK, lead with the reason — usually market closed or daily cap breached. Don't bury it.

When verdict is WARN with calix_stale → say so explicitly: "Calix calendar is stale; treat as if a high-impact event may be imminent."

## Aggregation rules

- Verdict = strictest of all sub-statuses (`BLOCK > WARN > PASS`).
- A single BLOCK ⇒ BLOCK. The user's other rules don't override session-closed or daily-cap-breach.
- A single WARN ⇒ WARN even if six other checks pass. The user decides whether to proceed.
- Spread baseline updates AFTER the check completes — so the WARN reflects the *prior* baseline, and the new sample is folded in for the next call.

## Common pitfalls

- **Currency mapping for indices.** Use `_INDEX_TO_CURRENCIES` (in `cfd_skills.symbol_meta`) — NAS100 hits on USD news but not EUR; UKOIL hits on USD AND GBP. Send the union of relevant currencies as the `currencies` Calix filter.
- **Calix stale isn't always Calix's fault.** If Cloudflare KV refresh hasn't completed (e.g. DataPilot is rate-limited upstream), `stale: true` means "the data is older than freshness budget" — use it. The downstream WARN ensures the user doesn't take an entry assuming clean news data when there isn't any.
- **Same-symbol overlap is `WARN`, not `BLOCK`.** Stacking the same trade is a discipline call, not a hard rule. Surface the existing tickets so the user can decide.
- **Spread baseline cold-start.** First call for a symbol has no baseline → PASS with bootstrap reason. Don't WARN — the user hasn't given the EWMA enough samples to learn yet.
- **Earnings proximity is index-only.** A high-impact earnings beat on AAPL doesn't matter for XAUUSD; the check shortcuts to PASS for non-indices.
- **Candidate `risk_pct` is optional.** Without it, concurrent-budget shows current consumption but doesn't project. Pass it whenever the position-sizer was run upstream.
- **The agent picks the side; the checklist doesn't validate technical analysis.** It checks discipline rules, not whether the trade idea is good. Don't substitute it for chart reading.
