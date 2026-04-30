---
name: price-action
description: Use when the user asks for a price-action read, structural setup, bias on a symbol, or wants to see what setups are present right now. Triggers on phrases like "what's the setup on [symbol]", "is there a long setup on [symbol]", "give me a price action read on [symbol]", "show me the structure on [symbol]", "any swing setup on [symbol]", "bias on [symbol]". Composes per-TF pivots + S/R + FVG + OB + liquidity-pool detection with 9 setup detectors (pullback-EMA, S/R bounce, pin bar, engulfing, range-break retest, FVG fill, OB retest, liq-sweep reversal, BOS pullback) and emits 0-3 ranked candidates plus full structure for downstream pre-trade-checklist and position-sizer. Read-only / advisory — never executes.
---

# Price Action Read

Hybrid classical + ICT structural reader. Returns full structure (pivots, S/R, FVGs, OBs, liquidity pools, EMA stack, regime per TF) plus 0–3 ranked setup candidates. The skill never executes a trade; it hands off to [`pre-trade-checklist`](../pre-trade-checklist/SKILL.md) and [`position-sizer`](../position-sizer/SKILL.md) when the user wants to act on a candidate.

## When to invoke

- "what's the setup on XAUUSD"
- "is there a long setup on NAS100"
- "give me a price action read on UKOIL"
- "show me the structure on EURUSD"
- "any swing setup on USOIL today"
- "bias on XAGUSD"

Don't invoke for: pure quote/spread questions (use `mt5-market-data`), risk status (use `daily-risk-guardian`), session news (use `session-news-brief`).

## Inputs (collect from user if implied)

1. **Symbol** — required.
2. **Mode** — `intraday` (H1/M15/M5) or `swing` (D1/H4/H1). Default to `swing` if user phrasing is ambiguous; ask if they say "today" or "this week" without other clues.
3. **Timeframes override** — optional list `["H4", "H1", "M15"]` to override the mode default.

## Workflow

### 1. Resolve the timeframe stack

| Mode | Timeframes |
|---|---|
| `swing` (default) | `D1`, `H4`, `H1` |
| `intraday` | `H1`, `M15`, `M5` |
| explicit override | as supplied |

### 2. Fan out MCP tools (parallel)

For each timeframe in the stack:
- `mcp__mt5-mcp__get_rates(symbol=<sym>, timeframe=<tf>, count=200)`

Plus once per call:
- `mcp__mt5-mcp__get_quote(symbol=<sym>)` — current bid/ask
- `mcp__mt5-mcp__get_symbols(...)` to obtain `tick_size` and `digits` for the symbol

### 3. Build the bundle and pipe to the skill

```bash
echo '{
  "symbol": "XAUUSD",
  "mode": "swing",
  "timeframes": ["D1", "H4", "H1"],
  "as_of": "<now in ISO 8601>",
  "current_quote": <get_quote output>,
  "symbol_meta": {
    "tick_size": "<from get_symbols>",
    "digits": <from get_symbols>,
    "contract_size": "<from get_symbols>",
    "trade_mode": "<from get_symbols>"
  },
  "rates": {
    "D1": <get_rates D1 output>,
    "H4": <get_rates H4 output>,
    "H1": <get_rates H1 output>
  }
}' | trading-agent-skills-price-action
```

### 4. Read the JSON output

The result includes:
- `regime` per TF and `mtf_alignment` (`aligned_long`, `aligned_short`, `mixed`, `conflicted`)
- Full `structure` (pivots, S/R levels, FVGs, OBs, liquidity pools, EMA stack)
- `setups` — 0..3 ranked candidates, each with `id`, `type`, `side`, `entry_zone`, `suggested_entry`, `invalidation`, `stop_distance`, `confluence`, `structural_score`, `narrative_hint`
- `recent_bars_window` — last 20 bars on the two lowest TFs
- `warnings` — `sparse_bars_<TF>`, `mtf_conflict`, `no_clean_setup`, etc.

### 5. Pick a setup and write the rationale

If `setups` is non-empty, examine each candidate's `confluence`, `structural_score`, and `narrative_hint`:
- Pick the rank-1 setup unless its narrative conflicts with a fact you can see (e.g. an obvious news event mid-bar).
- Write a `selection_rationale` string — one or two sentences explaining *why* this setup over the others.
- Set `selected_setup_id` to the chosen `id` (or `"stand_aside"` if you decide to skip even though candidates exist).

If `setups` is empty (or `warnings` contains `no_clean_setup`):
- Set `selected_setup_id` to `"stand_aside"` and `selection_rationale` accordingly.
- Render the structure narrative anyway (where price is relative to S/R, unfilled FVGs, etc.) — this is still useful context.

### 6. Narrate

Render a concise summary covering:
1. **Regime + MTF alignment** — one line.
2. **Key levels** — nearest S/R, unfilled FVGs, unswept liquidity.
3. **Selected setup** — type, side, entry, invalidation, narrative.
4. **Hand-off offer** — "Want me to run pre-trade-checklist + position-sizer for this entry?"

### 7. (Optional) Hand-off to checklist + sizer

If the user accepts the hand-off:
- For `pre-trade-checklist`: pass `{symbol, side, entry: suggested_entry, stop: invalidation}`.
- For `position-sizer`: pass `{symbol, side, stop_distance}` (plus the user's account/quote bundle). The setup's `stop_distance` field maps directly to the sizer's request — no manual tick-size conversion needed.

## Health & degradation

- Missing bars on a TF → `warnings: ["missing_bars_<TF>"]`. Skill still runs on the available TFs.
- <60 bars on a TF → `warnings: ["sparse_bars_<TF>"]`. Detectors needing longer lookback skip silently.
- Conflicting MTF → `mtf_conflict` warning + scores penalised.
- No tradeable setup → `no_clean_setup` warning + empty `setups`. Narrative becomes "stand aside".

## Common pitfalls

- **Don't pipe stale bars.** If `as_of` is significantly older than the most recent bar's `time`, the skill has no way to detect that — make sure your fan-out happens just before the pipe.
- **Don't pre-filter the candidates.** Pass the full output to the user; the LLM's job is to *choose*, not to censor.
- **Don't combine modes.** Pick `swing` or `intraday`; if the user wants both, run twice.
