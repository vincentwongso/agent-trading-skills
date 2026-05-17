---
name: cot-crowdedness
description: Use when checking whether a symbol's speculator positioning is at a contrarian extreme. Reads cached CFTC Disaggregated COT data and returns a crowded_long / crowded_short / neutral tag plus percentile rank. Drives Shapiro-style contrarian-fade setups. Read-only — never executes a trade.
---

# COT Crowdedness

Reads the CFTC Disaggregated Futures-Only Commitment of Traders report and
scores managed-money net positioning per symbol against its trailing 3-year
distribution. Returns a `crowded_long` / `crowded_short` / `neutral` tag that
contrarian playbooks (see `strategies/crowded-fade.md`) use as their
positioning-extreme filter.

**This skill is positioning data only.** The trade trigger is a separate
price-action pattern (see `failure_swing` detector in `price-action`). Never
fade a market on positioning alone.

## Prerequisites & first-run setup

1. **`trading-agent-skills-cot` CLI is on PATH.** Test with `trading-agent-skills-cot list`.
2. **The cache must be populated** for the symbol you're asking about. Run
   `trading-agent-skills-cot refresh` on a weekly cron (Friday after 16:00 ET,
   when CFTC publishes the new report).

The CLI fetches from `https://publicreporting.cftc.gov/resource/72hh-3qpy.json`
— free, no auth, ~156 weekly rows per symbol. Cache lives at
`~/.trading-agent-skills/cot_cache/<symbol>.json`.

## Supported symbols

Run `trading-agent-skills-cot list` for the live mapping. Currently:

- **Oil** — `USOIL` (CL NYMEX)
- **Metals** — `XAUUSD` (GC COMEX), `XAGUSD` (SI COMEX)
- **Indices** — `NAS100` (NQ), `SPX500` (ES), `US30` (YM)
- **FX majors** — `EURUSD` (6E), `GBPUSD` (6B), `USDJPY` (6J inverse),
  `AUDUSD` (6A), `USDCAD` (6C inverse)

For inverse symbols (USDJPY, USDCAD) the tag is already flipped — `crowded_long`
on USDJPY means "crowd is long USDJPY," even though the underlying contract is
JPY futures.

Not supported (need separate provider): `UKOIL` (ICE not CFTC), `GER40` (no
direct futures), share CFDs, crypto. See "Future extensions" below.

## When to invoke

- "is gold crowded?" / "what's the COT positioning on XAUUSD?"
- "are spec longs piled into oil right now?"
- Stage 1 morning-brief / weekend-review — surface tags for the watchlist.
- Stage 2 setup-evaluation under the crowded-fade playbook — gate the entry.

Don't invoke for: price action, news, technical levels — those have their own skills.

## Workflow

### 1. Refresh (weekly cron, not per query)

```bash
# Refresh ALL mapped symbols
trading-agent-skills-cot refresh

# Refresh one symbol
trading-agent-skills-cot refresh --symbol XAUUSD
```

Output: `{"refresh": [{"symbol": "...", "cached_at": "...", "n_entries": 156}, ...]}`.
Partial failures are surfaced per symbol; exit code 2 on any network error.

Recommended cron: **Friday 16:30 ET** (Saturday 06:30 AEST in the openclaw
workspace) — CFTC publishes new data at 15:30 ET Friday.

### 2. Get crowdedness for a symbol (per query, no network)

```bash
trading-agent-skills-cot get --symbol XAUUSD
```

Returns:

```json
{
  "symbol": "XAUUSD",
  "contract_code": "088691",
  "contract_label": "GOLD",
  "as_of": "2026-05-13T00:00:00+00:00",
  "latest_net": "187432",
  "percentile": "94.2",
  "tag": "crowded_long",
  "weeks_growing": 3,
  "lookback_weeks": 156,
  "inverse": false
}
```

### 3. Apply the tag

| `tag` + `weeks_growing` | Crowded-fade playbook |
|---|---|
| `crowded_long` AND `weeks_growing >= 3` | Eligible for **short** fade — confirm with failure pattern |
| `crowded_short` AND `weeks_growing >= 3` | Eligible for **long** fade — confirm with failure pattern |
| `neutral` | Not eligible. Stand down. |
| Crowded but `weeks_growing < 3` | Crowd is unwinding — momentum-fade window may already be closing. Skip. |

The `weeks_growing` field implements the "growing for at least 3 of the last 4
weekly reports" filter from the strategy spec — confirms the crowd is still
piling in, not exiting.

## Health & degradation

- **No cache for symbol** → CLI returns `{"error": "no_cache"}` with the
  refresh hint. Run the refresh command, then retry.
- **Stale cache** (`as_of` > 10 days old) → tag is still computed but the agent
  should warn the user and trigger a refresh.
- **Socrata network error during refresh** → partial success; symbols that
  succeeded are cached, failed ones return per-symbol errors in the response.
- **Inverse symbols** — caller doesn't need to flip; the skill already returns
  the symbol-side tag.

## Future extensions

The module ships with a `CrowdednessProvider` protocol so contrarian-signal
sources beyond CFTC can be added as siblings:

- **FXSSI retail sentiment** — > 75% one-sided retail = contrarian signal.
  Useful for `GER40` (no CFTC) and crosses. Would be a new
  `retail_sentiment.py` module + CLI.
- **AlphaVantage options expiry / open-interest** — extreme call/put OI ratios
  near monthly expiry = positioning pressure proxy. Useful for `SPX500`,
  `NAS100`, single-name equities. Would compose AV MCP directly (no separate
  fetcher needed — see `equity-fundamentals` for the AV-MCP pattern).

Both are scoped as separate tickets; this skill is COT-only for v1.

## Common pitfalls

- **Don't fade on positioning alone.** The CFTC has been "crowded long" on
  gold for months at a time. Always pair with a price-failure trigger.
- **Don't trust a refresh older than a week.** New COT prints Friday 15:30 ET;
  data older than that is by definition stale.
- **Don't confuse `inverse: true` with a bug.** USDJPY and USDCAD are quoted
  inverse to the underlying futures — the skill flips for you. The
  `contract_code` field tells you what's underneath.
