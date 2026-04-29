---
name: daily-risk-guardian
description: Use when the user asks about today's risk status, whether they're clear to take another trade, how much they've lost today, or to assess at-risk vs risk-free open positions against their daily loss cap. Triggers on phrases like "what's my risk status today", "am I clear to take another trade", "how much have I lost today", "what's my drawdown right now", "show me the daily guardian". Composes today's realized P&L (since NY 4pm ET) with the worst-case drawdown from open AT_RISK positions and surfaces a CLEAR / CAUTION / HALT verdict. Read-only / advisory — never executes.
---

# Daily Risk Guardian

Tracks today's realized + unrealized exposure against a daily loss cap, with positions classified as `AT_RISK`, `RISK_FREE`, or `LOCKED_PROFIT` (LLM-judged from SL location, news, structure). "Today" = since the most recent NY 4pm ET close (= 6am AEST), DST handled by `zoneinfo`. The skill never executes orders — output is informational.

## When to invoke

Trigger phrases:
- "what's my risk status today"
- "am I clear to take another trade"
- "how much have I lost today"
- "show me the daily guardian"
- "what positions am I at risk on right now"

Use **before** suggesting a new entry — the [`pre-trade-checklist`](../pre-trade-checklist/SKILL.md) skill composes guardian internally for that flow.

## Inputs (collect from MCP)

The skill is fed a JSON bundle the agent assembles from these calls:

1. `mcp__mt5-mcp__get_account_info` → equity, balance, currency.
2. `mcp__mt5-mcp__get_positions` → all currently-open positions.
3. For each unique open-position symbol: `mcp__mt5-mcp__get_symbols(category=...)` filtered to that symbol → tick_size, tick_value (deposit ccy / tick / lot).
4. `mcp__mt5-mcp__get_history(from_ts=last_reset_utc, to_ts=now_utc)` → today's closed deals. Sum `deal.profit + deal.swap + deal.commission` to get `realized_pnl_today` (deposit ccy).

## Classifying open positions (LLM-judged)

For **each** open position, decide AT_RISK / RISK_FREE / LOCKED_PROFIT before bundling. Use this contract — false-positive RISK_FREE is the dangerous error:

| Classification | Test | Examples |
|---|---|---|
| `RISK_FREE` | SL is at or beyond entry — a stop hit returns ≥ entry | SL trailed to entry on a long (SL == entry); SL on a short raised to entry |
| `LOCKED_PROFIT` | SL is far enough beyond entry to clear commission + spread on close | Long with SL 30 pts above entry on a 10-pt-spread, $5 commission instrument |
| `AT_RISK` | Default. SL below entry on a long, above entry on a short, or no SL set | Fresh trade, partially-trailed stop that's still adverse |

**Soft-RISK_FREE override (use sparingly):** A trailing stop sitting just below a confluent technical level (e.g. higher-low after consolidation) may be RISK_FREE even if not yet mathematically breakeven, *if* the structure is unlikely to break first. Surface the reasoning in `classification_reason`.

**Catalyst-pending positions** (e.g. UKOIL long with Strait of Hormuz tension intact) are AT_RISK by default — a structural-thesis bet doesn't make the cash any safer.

When uncertain → AT_RISK. The user can manually override in the journal afterwards.

## Bundle shape

```json
{
  "now_utc": "2026-04-29T21:00:00+00:00",
  "account": { /* get_account_info output */ },
  "positions": [
    {
      "position": { /* one entry from get_positions */ },
      "symbol":   { /* matching get_symbols entry */ },
      "classification": "AT_RISK",
      "classification_reason": "Fresh entry; SL 200pts below at structure low."
    }
  ],
  "realized_pnl_today": "150.00"
}
```

Optional override paths (used by tests; defaults to `~/.cfd-skills/{config.toml,daily_state.json}`):

```json
{ "config_path": "/path/to/config.toml", "state_path": "/path/to/state.json" }
```

## Run

```bash
echo '<bundle>' | python -m cfd_skills.cli.guardian
```

Or via the entry point: `cfd-skills-guardian`.

## Reading the result

```json
{
  "status": "CLEAR | CAUTION | HALT",
  "deposit_currency": "USD",
  "equity": "10000.00",
  "session_open_balance": "10000.00",
  "realized_pnl_today": "150.00",
  "unrealized_pnl": "210.00",
  "at_risk_combined_drawdown": "300.00",
  "worst_case_loss": "150.00",
  "worst_case_loss_pct_of_session": "1.50",
  "daily_loss_cap_pct": "5.0",
  "caution_threshold_pct": "2.50",
  "concurrent_risk_budget_pct": "5.0",
  "concurrent_risk_consumed_pct": "3.00",
  "positions": [...],
  "flags": [...],
  "notes": [...],
  "session_just_reset": false,
  "next_reset_utc": "2026-04-30T20:00:00+00:00",
  "seconds_until_next_reset": 82800
}
```

Render to the user with status front-and-center, then specifics:

```
CLEAR — worst case 1.5% of session (cap 5%, caution at 2.5%).
Realized today: +$150. Unrealized: +$210.
At-risk combined: $300 (3% of $10k).
Concurrent budget used: 3% / 5%.
Open positions:
  XAUUSD long 0.5 — AT_RISK, drawdown to SL $300 (3%); SL at structure low.
  NAS100 short 0.2 — RISK_FREE, SL trailed to entry.
Next reset: 6:00 AM AEST tomorrow.
```

When status == HALT, lead with the explicit "no new entries today" sentence. When CAUTION, surface which threshold triggered (loss-pct vs concurrent-budget — they can fire independently if the user has set the budget tighter than the loss cap).

## Output flags (machine-readable)

| Flag | Meaning |
|---|---|
| `DAILY_CAP_BREACHED` | Worst-case loss ≥ daily cap → HALT |
| `DAILY_CAP_CAUTION` | Worst-case loss ≥ caution threshold |
| `CONCURRENT_BUDGET_BREACHED` | Sum of AT_RISK risk % > concurrent budget |
| `AT_RISK_POSITION_HAS_NO_STOP` | An AT_RISK position has SL=0; worst-case math undercounts |
| `OVERNIGHT_FINANCING` | Position(s) crossed at least one swap roll — see swap_accrued |
| `INVALID_SESSION_OPEN_BALANCE` | Defensive flag if session_open ≤ 0 |

## Common pitfalls

- **Naive datetime in `now_utc`** — the bundle must carry tz-aware UTC. The CLI rejects naive values.
- **Closing-deal P&L attribution.** mt5 reports `profit`, `swap`, and `commission` as separate fields on each deal. Sum all three for the cash that actually hit the account.
- **Holiday gaps in get_history.** If the last reset was Friday 4pm ET and now is Monday 9am ET, weekend swap will appear in history-window deals. That's correct — Friday's 3x swap charge is realized cash.
- **First-call session_open.** If `~/.cfd-skills/daily_state.json` doesn't exist, the first guardian call records the *current* equity as session-open and reports `session_just_reset: true`. That's expected. Don't run guardian for the first time mid-drawdown unless you intend to anchor the session at that point.
- **Risk-free predicate is LLM-judged, not a fixed math rule.** Take the time to actually evaluate each open position before classifying — the math is downstream of the classification. A wrong RISK_FREE call hides real exposure from the cap.
