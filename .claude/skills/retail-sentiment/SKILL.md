---
name: retail-sentiment
description: Use when checking retail positioning crowdedness via FXSSI on symbols where CFTC COT doesn't reach (GER40, UKOIL, crypto, exotic FX). Returns a crowded_long / crowded_short / neutral tag at the 75% one-sided threshold. Companion to cot-crowdedness for the Shapiro-style contrarian-fade playbook. Read-only — never executes a trade.
---

# Retail Sentiment (FXSSI)

Reads cached FXSSI retail-broker positioning (% of clients long vs short) and
scores symbols against a one-sided **75% threshold**. Sibling provider to
`cot-crowdedness` — fills the gap where CFTC COT doesn't apply:

- **`GER40`** — no direct CFTC contract
- **`UKOIL`** — Brent trades on ICE, not CFTC
- **`BTCUSD`** and other crypto CFDs
- **Exotic FX crosses** (`EURGBP`, etc.) with no reportable speculator data

Implements the `CrowdednessProvider` protocol from
`trading_agent_skills.cot_crowdedness`, so consumers can blend FXSSI with COT
or fall back when COT is silent.

**This is a WEAKER signal than COT.** Retail-broker positioning is noisier
than CFTC managed-money positioning. Per `strategies/crowded-fade.md`,
**halve position size** when FXSSI is the sole crowdedness source. Never fade
on positioning alone — wait for the price-failure trigger
(`failure_swing` in `price-action`).

## Prerequisites & first-run setup

1. **`trading-agent-skills-retail-sentiment` CLI is on PATH.** Test with
   `trading-agent-skills-retail-sentiment list`.
2. **The cache must be populated** for the symbol you're asking about. Run
   `trading-agent-skills-retail-sentiment refresh` on a regular cron (FXSSI
   updates intraday — every 1–4h is reasonable; the refresh merges new
   snapshots into the cache to accumulate history for the growing-side filter).

Cache lives at `~/.trading-agent-skills/retail_sentiment_cache/<symbol>.json`.

### Endpoint (verified 2026-05-17)

Single bulk endpoint: `GET https://fxssi.com/api/current-ratios` returns
JSON with `.pairs.<slug>.average` (mean pct_long across reporting brokers)
and a `formed` unix-epoch timestamp. One call covers all 25 mapped pairs —
the CLI's bulk `refresh` (no `--symbol`) uses exactly one network call.

No auth, no rate-limit observed at the cadence we use (every 2–4h).

The CDN mirror at `https://c.fxssi.com/api/current-ratios` returns a
subset of pairs (currently FX + crypto only); the canonical `fxssi.com`
host covers all 25.

## Supported symbols

Run `trading-agent-skills-retail-sentiment list` for the live mapping:

- **Indices** — `GER40`, `NAS100`, `SPX500` (slug `SP500`), `US30`
- **Metals** — `XAUUSD`, `XAGUSD`
- **Energy** — `USOIL` (slug `XTIUSD`), `UKOIL` (slug `XBRUSD`)
- **FX majors** — `EURUSD`, `GBPUSD`, `USDJPY`, `AUDUSD`, `USDCAD`
- **FX crosses** — `EURGBP`
- **Crypto** — `BTCUSD`

Unlike COT, the tag is **already symbol-side** — there's no inverse-contract
flip. FXSSI reports "% of retail clients long the symbol", which is what we
score directly.

## When to invoke

- "is retail crowded on GER40?" / "what's FXSSI showing for BTCUSD?"
- "any contrarian setup on UKOIL right now?"
- Stage 1 morning-brief — surface tags for the watchlist on COT-unmappable
  symbols.
- Stage 2 setup-evaluation under the crowded-fade playbook — gate the entry
  on `GER40`, `UKOIL`, crypto, exotic FX.

Don't invoke for: COT-mapped symbols where `cot-crowdedness` is the
authoritative source (oil-WTI, metals on COMEX, FX majors, indices on CME) —
that gives a stronger signal. Use this as a **fallback** for the symbols COT
can't see.

## Workflow

### 1. Refresh (cron, not per query)

```bash
# Refresh ALL mapped symbols
trading-agent-skills-retail-sentiment refresh

# Refresh one symbol
trading-agent-skills-retail-sentiment refresh --symbol GER40
```

Output: `{"refresh": [{"symbol": "...", "cached_at": "...", "n_entries": N}, ...]}`.
The bulk form does **one** network call and writes per-symbol caches; if a
symbol is missing from the response (rare broker outages), it appears with
`"error": "not_in_response"` so coverage gaps are surfaced.

The refresh **merges** new snapshots with the existing cache (deduped by
timestamp) so history accumulates over time — FXSSI exposes only the
current snapshot, and the growing-side filter needs prior samples.

Recommended cadence: **every 2–4 hours** (or any time before Stage 1's
morning brief). Exit code 2 on network error.

### 2. Get crowdedness for a symbol (per query, no network)

```bash
trading-agent-skills-retail-sentiment get --symbol GER40
```

Returns the same `Crowdedness` shape `cot-crowdedness` produces (so consumers
that blend providers don't branch on source):

```json
{
  "symbol": "GER40",
  "contract_code": "fxssi:GER40",
  "contract_label": "FXSSI Retail Sentiment GER40",
  "as_of": "2026-05-17T12:00:00+00:00",
  "latest_net": "60",
  "percentile": "80",
  "tag": "crowded_long",
  "weeks_growing": 3,
  "lookback_weeks": 8,
  "inverse": false
}
```

The `percentile` field is **repurposed** from COT semantics: for FXSSI it's
the actual `pct_long` (or `pct_short`) at the latest snapshot, not a rank
within a distribution. The `contract_code` is `"fxssi:<symbol>"` so blends
can distinguish source.

### 3. Apply the tag

| `tag` + `weeks_growing` | Crowded-fade playbook (FXSSI-only — half size) |
|---|---|
| `crowded_long` AND `weeks_growing >= 3` | Eligible for **short** fade — confirm with failure pattern, size at 50% |
| `crowded_short` AND `weeks_growing >= 3` | Eligible for **long** fade — confirm with failure pattern, size at 50% |
| `neutral` | Not eligible. Stand down. |
| Crowded but `weeks_growing < 3` | Crowd is unwinding — skip. |

The `weeks_growing` adapts to short series: if you only have N snapshots
(N < 5), it counts deltas across all available pairs (max N − 1).

## Health & degradation

- **No cache for symbol** → CLI returns `{"error": "no_cache"}` with the
  refresh hint. Run the refresh command, then retry.
- **Unmapped symbol** → CLI returns `{"error": "unmapped_symbol"}`. Add to
  `FXSSI_SYMBOL_MAP` if applicable.
- **Endpoint shape mismatch on refresh** → `parse_response` raises
  `ValueError` (top-level shape) or silently skips malformed pairs (per-pair
  shape). Network errors exit 2.
- **Stale cache** (`as_of` more than 24h old) → tag is still computed but
  the agent should warn the user and trigger a refresh.

## Common pitfalls

- **Don't fade on positioning alone.** This is a weaker signal than COT and
  needs a price-failure trigger to fire (`failure_swing`).
- **Don't full-size on FXSSI-only signals.** Halve the standard
  crowded-fade allocation per `strategies/crowded-fade.md`.
- **Don't conflate `contract_code` with COT codes.** A `"fxssi:GER40"`
  prefix is the marker; consumers blending COT + FXSSI rely on this to
  distinguish sources.
