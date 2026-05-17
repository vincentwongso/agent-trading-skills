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

### Endpoint caveat (UNVERIFIED)

FXSSI does **NOT** publish a documented public JSON API. The fetcher's
default endpoint (`https://fxssi.com/api/sentiment/<slug>`) is a best-effort
guess and may need adjustment. The same applies to the per-symbol slug map
(e.g. `GER40` → `DE30`, `UKOIL` → `BRENT`, `NAS100` → `NASDAQ100`).

If `refresh` fails with a 404 / shape mismatch, this is the first thing to
fix. The function **signature** is stable — only the URL and the JSON-row
parser inside `fetch_fxssi` should need updating. See the `TODO` markers in
`src/trading_agent_skills/retail_sentiment.py`.

A future maintainer may need to switch from a JSON endpoint to an HTML scrape
(BeautifulSoup) once FXSSI's actual surface is confirmed.

## Supported symbols

Run `trading-agent-skills-retail-sentiment list` for the live mapping:

- **Indices** — `GER40` (DE30), `NAS100`, `SPX500`, `US30` (DJ30)
- **Metals** — `XAUUSD`, `XAGUSD`
- **Energy** — `USOIL` (WTI), `UKOIL` (BRENT)
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
The refresh **merges** new snapshots with the existing cache so history
accumulates over time (FXSSI exposes the current snapshot, not a backfill —
the growing-side filter needs prior samples).

Recommended cadence: **every 2–4 hours** (or any time before Stage 1's
morning brief). Partial failures are surfaced per symbol; exit code 2 on any
network error.

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
- **Endpoint shape mismatch on refresh** → fetcher raises `ValueError`; the
  CLI surfaces it as a per-symbol error and exits 2. See the endpoint caveat
  above — this is the first thing to fix when FXSSI is brought online.
- **Stale cache** (`as_of` more than 24h old) → tag is still computed but
  the agent should warn the user and trigger a refresh.

## Common pitfalls

- **Don't fade on positioning alone.** This is a weaker signal than COT and
  needs a price-failure trigger to fire (`failure_swing`).
- **Don't full-size on FXSSI-only signals.** Halve the standard
  crowded-fade allocation per `strategies/crowded-fade.md`.
- **Don't trust the cache without confirming the endpoint.** The default
  fetcher URL is unverified — see the endpoint caveat above.
- **Don't conflate `contract_code` with COT codes.** A `"fxssi:GER40"`
  prefix is the marker; consumers blending COT + FXSSI rely on this to
  distinguish sources.
