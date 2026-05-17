---
name: options-crowdedness
description: Use when checking whether listed-options positioning on a US equity or index is at a contrarian extreme. Reads AlphaVantage MCP options chains via agent fan-out, returns a crowded_long / crowded_short / neutral tag plus put/call OI percentile rank and pin-risk flag. Triggers on "options positioning on X", "put/call OI extreme on SPY", "is the options crowd one-sided on QQQ", "any pin risk on SPX500 this week", "fade-grade options crowd on NVDA". Read-only — never executes a trade.
---

# Options Crowdedness

Companion to [`cot-crowdedness`](../cot-crowdedness/SKILL.md). Reads AlphaVantage
listed-options chains and scores the put/call open-interest ratio against the
symbol's trailing distribution. Returns a `crowded_long` / `crowded_short` /
`neutral` tag plus a *pin-risk* flag when the nearest monthly expiry is within
a week.

COT is positioning *magnitude* (managed-money net vs trailing distribution);
options OI is positioning *flavour* (where the hedging / speculation is
concentrated and on which side). They are **complementary, not redundant** —
on indices the two often disagree and the disagreement is itself a signal.

**This skill is positioning data only.** The trade trigger is a separate
price-action pattern (see `failure_swing` detector in `price-action`). Never
fade a market on positioning alone.

## Prerequisites

1. **AlphaVantage MCP server is configured.** Verify with
   `mcp__alphavantage__PING`. If missing, tell the user the skill needs the
   AV MCP server added to their MCP config.
2. **`trading-agent-skills-options-crowdedness` CLI is on PATH** (entry point
   shipped with this package).

## When to invoke

- "is SPY/SPX500 options-crowded right now?"
- "put/call OI extreme on NVDA?"
- "any pin risk on SPX500 this week?"
- Stage 1 morning-brief on indices and single names with rich options markets.
- Stage 2 setup-evaluation under the crowded-fade playbook — gate the entry
  *alongside* COT (when both agree, conviction is much higher).

Don't invoke for: futures-only symbols (`USOIL`, `XAUUSD`, FX majors — use
`cot-crowdedness`), Brent/`UKOIL` (no AV options), or anything without listed
US options.

## Symbols it makes sense for

- **Indices** — `SPX500` (SPY), `NAS100` (QQQ), `US30` (DIA) via the
  underlying ETF chain. Pass the ETF symbol to the AV MCP call.
- **Mega-caps & high-vol single names** — AAPL, MSFT, NVDA, TSLA, META, AMZN,
  GOOGL, etc. Anything with chain volume.
- **Skip** — small caps, OTC, non-US listings, anything where AV returns < 50
  contracts on a typical day.

## Workflow

### 1. Resolve the AV symbol

For `SPX500` / `NAS100` / `US30` use the ETF ticker (SPY / QQQ / DIA). For
single names use the cash ticker directly. If ambiguous, fall back to
`mcp__alphavantage__SYMBOL_SEARCH(keywords=...)`.

### 2. Fan out MCP calls (parallel)

- `mcp__alphavantage__REALTIME_OPTIONS(symbol=<sym>)` — current chain with
  open-interest per contract. This is the *snapshot* input.
- `mcp__alphavantage__HISTORICAL_OPTIONS(symbol=<sym>, date=<YYYY-MM-DD>)` —
  fan out once per trading day for ~60-90 prior days. Each historical
  snapshot is aggregated into a single put/call OI ratio for the *history*
  distribution.

The agent (Claude Code) does this fan-out — **the skill module never imports
MCP**. Bundle the responses below and pipe to the CLI.

### 3. Pipe the bundle to the CLI

```bash
echo '{
  "symbol": "SPX500",
  "as_of": "2026-05-17T16:00:00+00:00",
  "options_chain": {
    "symbol": "SPY",
    "data": [
      {"contract_type": "call", "open_interest": "12345",
       "expiration": "2026-06-19", ...},
      {"contract_type": "put",  "open_interest": "9876",
       "expiration": "2026-06-19", ...}
    ]
  },
  "history": [
    {"as_of": "2026-02-17T16:00:00+00:00", "put_call_oi_ratio": "0.82"},
    {"as_of": "2026-02-18T16:00:00+00:00", "put_call_oi_ratio": "0.85"},
    ...
  ],
  "pin_risk_days": 7
}' | trading-agent-skills-options-crowdedness
```

### 4. Read the response

```json
{
  "symbol": "SPX500",
  "contract_code": "avopt:SPX500:pin",
  "contract_label": "OPTIONS OI",
  "as_of": "2026-05-17T16:00:00+00:00",
  "latest_net": "1.42",
  "percentile": "94.3",
  "tag": "crowded_short",
  "weeks_growing": 3,
  "lookback_weeks": 65,
  "inverse": false
}
```

### 5. Apply the tag

| `tag` + `weeks_growing` + `:pin`? | Crowded-fade playbook |
|---|---|
| `crowded_long` AND `weeks_growing >= 3` | Eligible **short** fade — confirm with failure pattern |
| `crowded_short` AND `weeks_growing >= 3` | Eligible **long** fade — confirm with failure pattern |
| `neutral` | Not eligible. Stand down. |
| Any tag with `:pin` suffix on `contract_code` | Within 1 week of monthly expiry — **dealer-gamma pinning risk**. Skip directional fades; price often gets glued to max-pain strike |
| Crowded but `weeks_growing < 3` | Crowd is already unwinding — fade window may be closing |

## The `avopt:<symbol>:pin` contract code

When `days_to_nearest_expiry <= 7` the skill suffixes `contract_code` with
`:pin`. This is the only signal the consumer needs to opt out of fade trades
into a high-gamma expiry. In that window:
- Index ETFs tend to pin to max-pain (the strike with largest combined OI).
- Single names with concentrated OI exhibit the same gravity.
- A crowded fade trade fights both the positioning *and* dealer hedging flow.

The plain `avopt:<symbol>` form (no `:pin`) signals positioning is the
dominant feature and a standard fade can be evaluated.

## Combining with COT

When both `cot-crowdedness` and `options-crowdedness` return the same tag on
an index (e.g. both `crowded_short` on SPX500), conviction is significantly
higher: spec futures hedgers AND options hedgers are aligned. Stage 2 may
upgrade the setup score.

When they **disagree** (e.g. COT `crowded_long`, options `crowded_short`),
it often means commercials are hedging an existing futures long. The
disagreement itself is a signal — don't auto-fade.

## Health & degradation

- **AV MCP server offline** → the agent should skip this skill and surface
  whatever positioning data COT provides instead.
- **No options listed for symbol** → empty `data[]` in the AV response.
  `from_av_chain` produces a zero-OI snapshot; the CLI still returns a
  result but `latest_net` is `0` and `percentile` is meaningless. Treat
  empty-chain as "no signal."
- **Short history (< 30 entries)** → `weeks_growing` may be 0 simply
  because we don't have enough deltas; don't read into it. Refresh and
  retry next session.

## Future extensions

- IV-skew percentile (25-delta put skew vs trailing distribution) as a
  second signal that often leads OI shifts.
- GEX (gamma exposure) at strike-level for finer pin-risk targeting.

Both are scoped as separate tickets; this skill is OI-only for v1.

## Common pitfalls

- **Don't fade on positioning alone.** Same rule as COT — pair with a
  price-failure trigger.
- **Don't ignore the `:pin` suffix.** Fading into a high-gamma weekly expiry
  is how account drawdowns are made.
- **Don't compare absolute p/c ratios across symbols.** Indices have
  structurally higher put/call OI than meme stocks; percentile vs the
  symbol's own history is the only fair comparison.
