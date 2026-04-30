---
name: trade-journal
description: Use when the user wants to log a completed trade, add a post-trade reflection to a previous trade, or query their performance history (win rate, R-multiple, P&L by setup or symbol, swap-harvest performance). Triggers on phrases like "journal this trade", "log my last [symbol] trade", "show me my trade journal for [period]", "what's my win rate on [setup type]", "how much swap have I earned this month", "add a note to my last trade". Works on a local append-only JSONL at ~/.trading-agent-skills/journal.jsonl — never mutates broker state.
---

# Trade Journal

Append-only JSONL journal of completed trades + retrospective reflections, plus performance analytics. The skill never executes trades — it records and queries. Schema is versioned (`schema_version: 1`) so future migrations are explicit.

## Prerequisites & first-run setup

This is the lightest-setup skill in the bundle:

1. **`trading-agent-skills-journal` CLI is on PATH.** Test with `trading-agent-skills-journal --help`. If not found: "Install the Python package — from the agent-trading-skills repo, run `pip install -e .` in a venv your harness can see."
2. **`~/.trading-agent-skills/journal.jsonl`** is auto-created on first `write`. Nothing to configure.
3. **`mt5-mcp` is OPTIONAL** — only needed for the auto-populate flow ("journal my last UKOIL trade" pulls fields from `get_history`). Manual writes work without it. If the user asks to auto-populate but `mt5-mcp` isn't connected, fall back to asking them for the trade fields directly.

No news API keys, no `config.toml`, no Calix dependency. Proceed straight to the workflow.

## When to invoke

**Write a new entry:**
- "journal this trade" / "log my last [symbol] trade"
- "record what I just closed on [symbol]"

**Update an existing entry:**
- "add a note to my last UKOIL trade"
- "I want to update my reflection on [trade]"

**Read / stats:**
- "show me my trade journal for this week"
- "what's my win rate on pullback-long?"
- "how am I doing on UKOIL this month?"
- "any swing-trade carry stats?"
- "what setup tags do I use?"

Do NOT invoke for live position questions — those go through `mt5-market-data` (`get_positions`, `get_history`).

## Writing a new entry

### Required fields (collect from user if missing)

| Field | Source / how to get it |
|---|---|
| `symbol` | broker symbol (e.g. `UKOIL`, `XAUUSD`) |
| `side` | `buy` or `sell` |
| `volume` | lot size (Decimal as string) |
| `entry_price` / `exit_price` | from `get_history` closing deal, or user-provided |
| `entry_time` / `exit_time` | UTC ISO 8601 (`2026-04-29T07:30:00+00:00`); from `get_history` deals |
| `original_stop_distance_points` | the stop distance at trade open, in symbol points; **must be > 0** for R-multiple math |
| `original_risk_amount` | denormalised cash equivalent of the stop in deposit currency. Compute as `original_stop_distance_points × tick_value × volume`. The sizer skill returns `cash_risk` — that's exactly this number for a fresh trade. |
| `realized_pnl` | from closing `Deal.profit` (deposit ccy) |
| `swap_accrued` | from closing `Deal.swap` (deposit ccy). Important: open positions don't expose swap reliably; pull from the closing deal. |
| `commission` | from closing `Deal.commission` (deposit ccy; usually negative) |
| `setup_type` | free-form tag. **Before writing, run `trading-agent-skills-journal tags` to load existing tags into context** and suggest "did you mean..." if the user types something close to one. |
| `rationale` | one paragraph: why entered |
| `risk_classification_at_close` | `AT_RISK`, `RISK_FREE`, or `LOCKED_PROFIT` (LLM-judged at close — see daily-risk-guardian; for the journal, ask the user or infer from SL position vs entry) |
| `ticket` (optional) | MT5 deal ticket if known |
| `outcome_notes` (optional) | post-trade reflection at write time. Can be added later via `update`. |

### Auto-populate from MT5 history

When the user says "journal my last UKOIL trade" without specifics:

1. Call `mcp__mt5-mcp__get_history(from_ts, to_ts, symbol="UKOIL")` with a window covering the last few days.
2. Identify the closing deal (the one that brought the position to flat). Use `type=sell` to close a buy, `type=buy` to close a sell. Pair with the opening deal by ticket lineage if needed.
3. Pull `entry_price`, `exit_price`, `entry_time`, `exit_time`, `volume`, `realized_pnl` (`profit`), `swap_accrued` (`swap`), `commission` from the deal pair.
4. Ask the user for the four fields MT5 doesn't carry: `original_stop_distance_points`, `setup_type`, `rationale`, `risk_classification_at_close`.

### Write the entry

```bash
echo '<bundle>' | trading-agent-skills-journal write --json
```

The CLI prints the assigned UUID. Confirm to the user: "Logged entry `abc-123` — net P&L $635.50, R-multiple 7.94."

## Updating an existing entry

User says "add a note to my last UKOIL trade":

1. `trading-agent-skills-journal query --symbol UKOIL --period week` → list with UUIDs and exit_times
2. Identify the entry (most recent by `exit_time`, or ask user if ambiguous)
3. `echo '{"uuid": "...", "outcome_notes": "..."}' | trading-agent-skills-journal update --json`

Patchable fields: `setup_type`, `rationale`, `risk_classification_at_close`, `outcome_notes`. Other fields are immutable — they describe the trade as it actually happened.

## Reading / stats

### List entries

```bash
trading-agent-skills-journal query [--period today|week|month|all] [--symbol X] [--setup-type T] [--side buy|sell] [--swing-only]
```

Output: JSON `{count, entries: [...]}`. Render to the user with key fields per row.

### Performance summary

```bash
trading-agent-skills-journal stats [filters] [--group-by setup_type|symbol|side|risk_classification|all] [--swing-only]
```

The summary carries:
- `count`, `win_count`, `loss_count`, `breakeven_count`, `win_rate` (% of decided trades)
- `total_pnl` = realized + swap + commission (the cash that hit the account)
- Components: `realized_pnl_total`, `swap_pnl_total`, `commission_total`
- `avg_r_multiple` (mean of net / original_risk_amount across trades)
- `expectancy_per_trade` = total_pnl / count

Win rate definition: a "win" means **net trade outcome > 0** (realized + swap + commission). A trade with negative directional P&L but positive swap that beats commissions still counts as a win — the journal cares about cash that hit the account, not directional thesis correctness.

### Swing-trade lens

`--swing-only` filters to trades where `|swap_accrued| > 0.2 × |realized_pnl|`. This isolates carry-driven trades from directional ones, so the user can answer "how is the swap-harvest book actually doing?" separately from "how is the directional book doing?".

### Tag suggestions

Before any `write`, call `trading-agent-skills-journal tags`. It returns existing setup_type values sorted by frequency. Use these to:
- Suggest reusing an existing tag if the user proposes a new one that's a near-typo (e.g. "pullback-lng" when "pullback-long" already exists 6 times)
- Pre-populate a dropdown of recent tags in the entry confirmation prompt

## Output rendering

Stats output is JSON; format it for the user:

```
This month — 14 trades:
  Win rate: 64.3% (9 wins / 5 losses)
  Net P&L: +$2,450.00 (directional +$1,800; swap +$700; commission -$50)
  Avg R-multiple: +1.85
  Expectancy: +$175 per trade

By setup:
  swap-harvest-long  4 trades, +$1,250, R 4.2 (carry-dominant)
  pullback-long      6 trades, +$890, R 1.4
  breakout-short     4 trades, +$310, R 0.5

By symbol:
  UKOIL              5 trades, +$1,400
  XAUUSD             4 trades, +$650
  NAS100             5 trades, +$400
```

Lead with the headline, follow with breakdowns. Surface anomalies the user might not notice — "your win rate on breakout-short is 25% over 12 trades; might be time to drop the setup or rethink the entry trigger."

## Schema notes

- All money / price / volume fields are **Decimal-as-string**. Float values are rejected at write time.
- All timestamps are **aware UTC ISO 8601**. The CLI converts non-UTC aware datetimes to UTC; naive datetimes are rejected.
- `original_stop_distance_points > 0` is enforced (zero would break R-multiple math).
- Updates are append-only patches; the journal file is never rewritten in place.
- Schema version 1 is current. Read-side rejects unknown versions explicitly so a future migration is impossible to miss.

## Common pitfalls

- **`commission` from open positions is unavailable.** mt5-mcp's `Position` model doesn't expose commission — pull it from the closing `Deal` via `get_history`. The plan's mt5-mcp enhancement note covered this.
- **Swap on partial closes.** Closing half a position generates a closing deal with proportional swap; the remaining half keeps accruing. Journal each closing deal as its own entry, or aggregate manually if the user wants a single record.
- **Risk classification at close.** If the user moved the SL to breakeven mid-trade, classify as `RISK_FREE` from the moment that move was made. If they trailed it past breakeven, `LOCKED_PROFIT`. Ask if unclear — don't guess.
- **Setup-type drift.** Free-form tags grow over time. Run `tags` periodically and ask the user about consolidating near-duplicates ("pullback-long" vs "pullback").
