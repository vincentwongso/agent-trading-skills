---
name: cfd-position-sizer
description: Use when the user asks how big a CFD/Forex/index trade should be, what lot size to use for a given risk, or wants a pre-trade sanity check on margin / swap / stop distance. Triggers on phrases like "size me into [symbol]", "what lot size for [N]% risk", "how big should I trade [symbol]", "lot size with stop at [price]", "is this stop too tight". Computes lot size from account equity + target risk + stop distance using the broker-authoritative tick_value, cross-checks margin against the broker's calc_margin tool, and surfaces an overnight-swap section. Read-only / advisory — never executes; use mt5-trading skill for execution.
---

# CFD Position Sizer

This skill computes a recommended lot size for a CFD / Forex / index / metals trade given (a) the user's account equity, (b) a target risk (% of equity OR absolute deposit-currency amount), (c) a stop distance (price level OR points), and (d) a side. It returns: recommended lot, broker-authoritative margin (with formula cross-check), cash-risk-at-stop, daily swap rates, sanity flags. It never mutates broker state — execution stays behind `mt5-trading`'s consent flow.

## When to invoke

Trigger phrases:
- "size me into [symbol] with [N]% risk, stop at [price]"
- "what lot size for [symbol] short with stop [N] pips away"
- "how big should I trade [symbol] if I want to risk $X"
- "is my stop too tight on [symbol]?"
- "what swap would I pay holding [symbol] for [N] nights?"

Don't invoke for general market-data questions — those go through `mt5-market-data` directly.

## Inputs (collect from the user if missing)

1. **Symbol** — required. If the user used a casual name ("gold", "the dow"), confirm the broker symbol (`XAUUSD`, `US30`) before proceeding.
2. **Side** — `long` or `short`.
3. **Risk** — exactly one of:
   - `risk_pct` (e.g. `1.0` for 1% of equity), OR
   - `risk_amount` (absolute deposit-currency amount).
4. **Stop distance** — exactly one of:
   - `stop_price` (absolute price level), OR
   - `stop_points` (distance in symbol points; 1 pip on a 5-digit FX pair = 10 points).
5. **Nights held** (optional, integer) — if the user is sizing a swing trade and wants the swap-aware section, ask for the planned holding nights. Default to `0` (skip swap-for-holding).

If the user's defaults are stored in `~/.cfd-skills/config.toml`, default `risk_pct` to `config.risk.per_trade_max_pct` (typically 1.0).

## Workflow

Run these MCP calls in this order. **All are read-only — call freely.**

### 1. Account + market state

Call all three in parallel:

- `mcp__mt5-mcp__get_account_info` → equity, free margin, leverage, deposit currency
- `mcp__mt5-mcp__get_quote(symbol)` → bid / ask
- `mcp__mt5-mcp__get_symbols(category=...)` and filter the result to the target symbol → contract_size, tick_size, **tick_value** (deposit ccy), volume_min / max / step, digits, calc_mode, swap_long / swap_short, swap_mode, margin_initial, stops_level, currency_profit, currency_margin

If the user passed `stop_price` outside the live spread, sanity-check it (e.g. `stop_price > ask` for a long is wrong) and surface a warning before proceeding.

### 2. Broker-authoritative margin (recommended)

Call `mcp__mt5-mcp__calc_margin(symbol=..., side=..., volume=..., price=...)` AFTER you have a candidate volume. Two-pass approach:

- First pass: run the sizer with `broker_margin=null` to get a candidate lot. Then call `calc_margin` with that lot. Then re-run the sizer with `broker_margin=<result>`. The second pass surfaces the cross-check note and the broker-authoritative margin-percent-of-free.
- Skip `calc_margin` only if the broker returns `MARGIN_CALC_FAILED` (market closed, exotic calc mode). The skill will fall back to formula margin and flag the limitation.

### 3. Run the sizer

Bundle the MCP outputs into a JSON object and pipe it to the local CLI:

```bash
echo '<bundle>' | python -m cfd_skills.cli.size
```

The bundle shape is documented in `src/cfd_skills/cli/size.py`. Use Decimal-as-string for all money / price / volume fields — that's mt5-mcp's serialisation contract and the CLI rejects floats.

### 4. Render the result

The CLI returns a JSON `SizingResult`. Translate it into a human-readable response:

- Lead with the recommendation: `Lot: 0.50; Stop: 1.0804 (200 pts); Cash risk: $100 (1.0% of equity)`
- Margin section: broker-authoritative if available, otherwise formula. Always show the percent of free margin consumed.
- Swap section (if `swap_for_holding` is non-null): "Holding 10 nights long: +$625 swap accrual. Daily rate: +$125/lot."
- Flags: each one gets a one-line explanation. Be specific. `BELOW_MIN_LOT` → "Computed lot 0.005 is below broker minimum 0.01; widen the stop, raise risk %, or skip."

Order the response so the user can scan: number first, justification second, warnings third.

### 5. Do NOT execute

This skill never calls a mutating tool. If the user wants to actually place the order, route them to `mt5-trading`. Confirm verbally: "Want me to place this? I'll route to mt5-trading for confirmation."

## Common pitfalls

- **Stop in pips vs. points.** On a 5-digit FX broker, 1 pip = 10 points. Always confirm whether the user said "20 pips" or "20 points" — they're 10x apart. The CLI takes points.
- **Margin currency mismatch.** For FX where margin currency differs from deposit (e.g. EURUSD with USD account → margin in EUR), the formula-vs-broker cross-check is *intentionally skipped*. That's not a bug; the skill notes it explicitly.
- **`stops_level` zero.** A broker that reports `stops_level = 0` accepts any stop distance — there's no minimum. That's fine; the spread-based sanity check still fires for tight stops.
- **Unsupported swap mode.** If the swap section is omitted with `SWAP_MODE_UNSUPPORTED`, surface the broker's `swap_mode` value to the user so they can verify manually with the broker.
- **Risk-free positions are out of scope** for this skill — it sizes new entries. The `daily-risk-guardian` skill handles the at-risk vs. risk-free classification of existing positions.

## Output contract

The CLI's JSON result has these fields (all Decimals serialised as strings):

| Field | Type | Notes |
|---|---|---|
| `symbol` | str | broker symbol |
| `side` | "long" \| "short" | |
| `lot_size` | str(Decimal) | floor-rounded to volume_step |
| `notional` | str(Decimal) | volume × contract_size × price (margin ccy) |
| `stop_price` | str(Decimal) | resolved from stop_points if applicable |
| `stop_distance_points` | int | |
| `cash_risk` | str(Decimal) | deposit ccy at the proposed stop |
| `risk_pct_of_equity` | str(Decimal) | |
| `margin_formula` | str(Decimal) | per EnCalcMode (margin ccy) |
| `margin_broker` | str(Decimal) \| null | from calc_margin (deposit ccy) |
| `margin_pct_of_free` | str(Decimal) \| null | uses broker value if available |
| `daily_swap_long_per_lot` | str(Decimal) | deposit ccy |
| `daily_swap_short_per_lot` | str(Decimal) | deposit ccy |
| `swap_for_holding` | str(Decimal) \| null | nights × side × lot, no 3x rollover |
| `flags` | list[str] | machine-readable codes |
| `notes` | list[str] | human-readable explanations |

Possible `flags`:
- `BELOW_MIN_LOT` / `ABOVE_MAX_LOT` — constraint violations
- `ZERO_RISK_PER_LOT` — stop too tight to compute risk
- `STOP_INSIDE_BROKER_MIN` — stop closer than `stops_level`
- `STOP_TIGHTER_THAN_2X_SPREAD` — likely whipsaw
- `HIGH_MARGIN_USAGE` — over `margin_warning_pct` of free margin
- `MARGIN_CROSS_CHECK_DRIFT` — formula and broker disagree by >2% (currencies match)
- `FORMULA_MARGIN_UNAVAILABLE` — calc_mode not supported locally
- `SWAP_MODE_UNSUPPORTED` — exotic swap_mode; section omitted
