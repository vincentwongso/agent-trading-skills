# price-action skill — design

**Status:** draft, awaiting user review
**Date:** 2026-04-29
**Author:** Claude (brainstorming session with Vincent)
**Sibling skills already shipped:** position-sizer, trade-journal, daily-risk-guardian, pre-trade-checklist, session-news-brief

---

## 1. Purpose

Provide a structured price-action read of a Forex/index/metals symbol from MT5 OHLC bars, emitting up to three ranked setup candidates plus the full underlying structure (pivots, S/R, FVGs, order blocks, liquidity pools, EMA stack, regime per timeframe). The output is consumed downstream by `pre-trade-checklist` and `position-sizer` for a coherent "is this a tradeable idea, and if so what size" workflow.

The skill is **read-only / advisory**. It never executes a trade. It is a peer to the existing four skills, not a sub-component of any of them.

## 2. Non-goals (v1)

- No chart image rendering. mt5-mcp returns no images and the LLM doesn't need them — structural detection from OHLC is more precise than pixel reading.
- No backtesting / replay. The skill reasons about the present.
- No tick-level analysis. Bar data only.
- No order-book / DOM analysis. mt5-mcp doesn't expose that.
- No automated execution.
- No constellation of every classical or ICT pattern. v1 detector menu is fixed at 9 (see §6); deferred items are listed in §15.

## 3. User-facing trigger contract

Frontmatter `description` for `.claude/skills/price-action/SKILL.md`:

> Use when the user asks for a price-action read, structural setup, bias on a symbol, or wants to see what setups are present right now. Triggers on phrases like "what's the setup on [symbol]", "is there a long setup on [symbol]", "give me a price action read on [symbol]", "show me the structure on [symbol]", "any swing setup on [symbol]", "bias on [symbol]". Composes per-TF pivots + S/R + FVG + OB + liquidity-pool detection with 9 setup detectors and emits 0-3 ranked candidates plus full structure for downstream `pre-trade-checklist` and `position-sizer`. Read-only / advisory — never executes.

## 4. Architecture

### 4.1 Two-layer split (matches existing repo pattern)

- **Python layer** (`src/trading_agent_skills/price_action/`): deterministic, Decimal-typed, no I/O at the package boundary. Detects structurally-valid candidates only — cannot hallucinate FVGs/OBs that don't satisfy formal definitions.
- **LLM layer** (in SKILL.md flow): selects which candidate is "the trade" (`selected_setup_id`), writes the human-readable narrative, writes a `selection_rationale` field that gets carried into `trade-journal` if the user takes the trade.

### 4.2 Sub-package, not a single file

This is the first sub-package in `trading_agent_skills/`. Justification: 9 detectors + 5 structure modules + scoring + schema is too much for a single file. Existing skills with single-file modules have ~300 lines; this would be ~1500. A sub-package keeps each detector independently testable.

### 4.3 Data flow

```
User phrase ("what's the setup on XAUUSD")
  ↓
Agent in SKILL.md flow
  ↓ infers mode: 'intraday' | 'swing' (default: 'swing'; user can override with explicit timeframes)
  ↓ resolves timeframes:
  │     intraday → ["H1", "M15", "M5"]
  │     swing    → ["D1", "H4", "H1"]
  │     override → user-supplied list
  ↓ MCP fan-out (parallel):
  │     get_rates(symbol, tf, count=200) for each tf in stack
  │     get_quote(symbol)
  │     get_symbols(...)              # to retrieve tick_size + digits for the symbol
  ↓
JSON bundle on stdin →  python -m trading_agent_skills.cli.price_action  → JSON on stdout
                              │
                              ├── decimal_io.D() coerces every numeric string → Decimal
                              ├── price_action.scan(bundle)
                              │       1. Per-TF structure: pivots → S/R → regime → EMA stack
                              │       2. FVG, OB, liquidity-pool detection (per TF)
                              │       3. Run 9 detectors → list[CandidateSetup]
                              │       4. Score each candidate (structural, deterministic)
                              │       5. Filter to ≤3 above quality_threshold
                              └── format(d, "f") avoids scientific notation on Decimal serialise
  ↓
JSON output (full structure + ranked candidates + selected_setup_id=null + selection_rationale=null)
  ↓
LLM in SKILL.md flow:
  - selects setup id (or "stand_aside" for empty list)
  - writes selection_rationale (auditable string)
  - writes prose narrative referencing structure + recent_bars_window
  - optional hand-off prompt: "want me to run pre-trade-checklist + position-sizer for this?"
```

## 5. Inputs (JSON bundle from agent)

```json
{
  "symbol": "XAUUSD",
  "mode": "swing",
  "timeframes": ["D1", "H4", "H1"],
  "as_of": "2026-04-29T03:42:00Z",
  "current_quote": { "bid": "2658.40", "ask": "2658.50", "time": "..." },
  "symbol_meta": {
    "tick_size": "0.01",
    "digits": 2,
    "contract_size": "100",
    "trade_mode": "full"
  },
  "rates": {
    "D1": [{"time": "...", "open": "...", "high": "...", "low": "...", "close": "...", "tick_volume": 12345, "spread": 4}, ...],
    "H4": [...],
    "H1": [...]
  },
  "config": {
    "quality_threshold": "0.45",
    "max_setups": 3,
    "scoring_weights": {
      "confluence": "0.35",
      "mtf_alignment": "0.30",
      "candle_quality": "0.20",
      "freshness": "0.15"
    }
  }
}
```

`config` block is optional; if omitted, the CLI reads from `~/.trading-agent-skills/config.toml` and falls back to shipped defaults.

## 6. Detector menu (v1: 9 detectors)

Each detector lives in `trading_agent_skills/price_action/detectors/<name>.py` and exposes `detect(bundle: ScanBundle, structure: Structure) -> list[CandidateSetup]`.

| # | Name | File | Layer | One-line trigger |
|---|---|---|---|---|
| 1 | Pullback to EMA stack | `pullback_ema.py` | Classical | EMA21/EMA50 aligned with regime + price retesting EMA21 |
| 2 | S/R bounce | `sr_bounce.py` | Classical | Test of clustered prior pivot + rejection candle |
| 3 | Pin bar at level | `pin_bar.py` | Classical | Pin bar (wick ≥ 2× body) coinciding with S/R or FVG |
| 4 | Engulfing at level | `engulfing.py` | Classical | Engulfing candle at S/R or FVG |
| 5 | Range break + retest | `range_break_retest.py` | Classical | N-bar range break + retest of broken edge |
| 6 | FVG fill | `fvg_fill.py` | ICT | Unfilled HTF FVG, price returning into it |
| 7 | OB retest | `ob_retest.py` | ICT | Last opposing candle before displacement, retest from displaced side |
| 8 | Liquidity sweep + reversal | `liq_sweep.py` | ICT | Sweep of prior swing high/low + reversal candle |
| 9 | BOS pullback | `bos_pullback.py` | ICT | HTF break-of-structure + LTF pullback to broken level |

## 7. Structure layer (always emitted)

- **Pivots** per TF — fractal detection with N-bar lookback/lookforward (default N=3; configurable). Each pivot tagged HH / HL / LH / LL based on local sequence.
- **S/R levels** per TF — clustered pivots within `tick_size × cluster_factor` (default cluster_factor=20). Each level carries `tested` count + last test time.
- **EMA stack** per TF — EMA21 + EMA50 on close. `aligned: bool` flag.
- **Regime** per TF — `trend_up | trend_down | range | transition`. Derived from pivot sequence + EMA alignment.
- **MTF alignment** — overall classification: `aligned_long | aligned_short | mixed | conflicted`.
- **FVGs** per TF — list of bullish (demand) and bearish (supply) gaps with fill state (`filled_pct`).
- **Order blocks** per TF — last opposing candle before displacement of ≥ `displacement_atr_mult × ATR`. Tracks retest state.
- **Liquidity pools** per TF — recent unswept swing highs (BSL) and lows (SSL). `swept: bool`.

## 8. Scoring formula

Each candidate gets a `structural_score ∈ [0, 1]`:

```
score = w_conf  × normalize(confluence_count, max=4)
      + w_mtf   × mtf_alignment_score
      + w_cand  × candle_quality_score
      + w_fresh × freshness_score
```

Default weights (overridable in `~/.trading-agent-skills/config.toml`):

- `confluence` — 0.35
- `mtf_alignment` — 0.30
- `candle_quality` — 0.20
- `freshness` — 0.15

Where:

- **confluence_count**: number of structure elements at the entry zone (S/R level + FVG + OB + liquidity pool nearby ⇒ 4).
- **mtf_alignment_score**: `aligned_*` ⇒ 1.0; `mixed` ⇒ 0.5; `conflicted` ⇒ 0.0.
- **candle_quality_score**: detector-specific, normalized to [0, 1] (e.g. for pin bar, wick:body ratio).
- **freshness_score**: how many bars since the structural feature became active (newer FVG / OB / unswept liquidity ⇒ higher).

Filter: emit only candidates with `score ≥ quality_threshold` (default 0.45). Cap at `max_setups` (default 3). Sort descending.

## 9. Output JSON schema

```json
{
  "schema_version": "1.0",
  "symbol": "XAUUSD",
  "mode": "swing",
  "timeframes": ["D1", "H4", "H1"],
  "as_of": "2026-04-29T03:42:00Z",
  "current_price": "2658.45",
  "regime": { "D1": "trend_up", "H4": "trend_up", "H1": "pullback" },
  "mtf_alignment": "aligned_long",
  "structure": {
    "pivots": { "D1": [...], "H4": [...], "H1": [...] },
    "sr_levels": [
      {"price": "2640.00", "tf": "H4", "tested": 3, "side": "support", "last_test": "..."}
    ],
    "ema_stack": {
      "H4": {"ema21": "2650.10", "ema50": "2625.30", "aligned": true, "direction": "up"}
    },
    "fvgs": [
      {"high": "2655.00", "low": "2652.00", "tf": "H1", "side": "demand", "filled_pct": "0.00", "created_at": "..."}
    ],
    "order_blocks": [
      {"high": "2645.00", "low": "2642.00", "tf": "H4", "side": "demand", "retested": false, "created_at": "..."}
    ],
    "liquidity_pools": [
      {"price": "2620.00", "tf": "H4", "type": "SSL", "swept": false, "created_at": "..."}
    ]
  },
  "setups": [
    {
      "id": "setup_1",
      "rank": 1,
      "type": "fvg_fill",
      "tf_setup": "H4",
      "tf_trigger": "H1",
      "side": "long",
      "entry_zone": {"low": "2652.00", "high": "2655.00"},
      "suggested_entry": "2653.50",
      "invalidation": "2648.50",
      "stop_distance": "5.00",
      "targets": ["2670.00", "2685.00"],
      "structural_score": "0.72",
      "confluence": ["H4_demand_FVG", "H1_swing_low", "EMA21_alignment"],
      "narrative_hint": "D1+H4 trend up + price pulling into unfilled H4 demand FVG; H1 trigger candle pending"
    }
  ],
  "selected_setup_id": null,
  "selection_rationale": null,
  "warnings": [],
  "recent_bars_window": {
    "H4": [last 20 bars],
    "H1": [last 20 bars]
  }
}
```

`selected_setup_id` and `selection_rationale` are written by the LLM in SKILL.md flow before any downstream hand-off. `selected_setup_id` is one of: a setup id from `setups[].id` (e.g. `"setup_1"`), the literal string `"stand_aside"` (when no candidate is worth taking, including but not limited to empty `setups[]`), or remains `null` only if the LLM step is skipped (e.g. when the skill is invoked purely for structure scanning). The `recent_bars_window` always covers the two lowest TFs in the active stack (e.g. swing → H4 + H1; intraday → M15 + M5).

## 10. Hand-off contract to existing skills

| Downstream skill | Fields it consumes |
|---|---|
| `pre-trade-checklist` | `symbol`, `selected_setup.side`, `selected_setup.suggested_entry` (as `entry`), `selected_setup.invalidation` (as `stop`) |
| `position-sizer` | `symbol`, `selected_setup.side`, `selected_setup.stop_distance` |
| `trade-journal` (entry log) | `selected_setup.type`, `selection_rationale`, `selected_setup.confluence`, `selected_setup.tf_setup`, `selected_setup.tf_trigger` |

## 11. Edge cases & error handling

| Condition | Behavior | Warning emitted |
|---|---|---|
| <60 bars on a TF | Detectors needing more lookback skip; structure still emitted | `sparse_bars_<TF>` |
| Last bar age > 2× TF | Detectors run; narrative flags freshness | `stale_data_<TF>` |
| Closed market (weekend, holiday) | Stale-bar logic; structure for next-session prep | `market_closed` |
| All detectors yield zero candidates | `setups: []`, `selected_setup_id: "stand_aside"` (LLM sets) | `no_clean_setup` |
| MTF signals conflict | Scores penalised, `mtf_alignment: "conflicted"` | `mtf_conflict` |
| `symbol_meta.trade_mode != "full"` | Structure still emitted | `symbol_not_tradeable` |
| Float in input rates | Reject at `decimal_io.D()` boundary, hard-error | (CLI exits non-zero) |

## 12. Components (sub-package layout)

```
src/trading_agent_skills/price_action/
  __init__.py             # re-exports scan, ScanBundle, ScanResult
  bars.py                 # Bar dataclass, MTF bundle, EMA(period)
  pivots.py               # fractal pivot detection, HH/HL/LH/LL classification
  structure.py            # S/R clustering, regime classification, MTF alignment
  fvg.py                  # bullish + bearish FVG detection, fill state
  order_block.py          # OB detection (last opposing candle before displacement)
  liquidity.py            # BSL / SSL pool detection, sweep state
  detectors/
    __init__.py           # registry of all 9 detectors
    pullback_ema.py
    sr_bounce.py
    pin_bar.py
    engulfing.py
    range_break_retest.py
    fvg_fill.py
    ob_retest.py
    liq_sweep.py
    bos_pullback.py
  scoring.py              # structural_score(candidate, structure) -> Decimal
  scan.py                 # orchestrator: scan(bundle) -> ScanResult
  schema.py               # TypedDict definitions for input + output

src/trading_agent_skills/cli/price_action.py   # JSON-stdin / JSON-stdout shim, calls scan()

.claude/skills/price-action/
  SKILL.md
  scripts/price_action.py
```

`pyproject.toml` entry point: `trading-agent-skills-price-action = "trading_agent_skills.cli.price_action:main"`.

## 13. Configuration

`~/.trading-agent-skills/config.toml` (existing, optional):

```toml
[price_action]
quality_threshold = "0.45"
max_setups = 3
default_mode = "swing"
intraday_timeframes = ["H1", "M15", "M5"]
swing_timeframes    = ["D1", "H4", "H1"]
bars_per_tf = 200

[price_action.scoring_weights]
confluence    = "0.35"
mtf_alignment = "0.30"
candle_quality = "0.20"
freshness     = "0.15"

[price_action.detectors.pivots]
lookback = 3

[price_action.detectors.sr]
cluster_factor = 20

[price_action.detectors.order_block]
displacement_atr_mult = "1.5"

[price_action.detectors.range]
min_bars = 8
```

All values Decimal-as-string where applicable. Defaults shipped in `trading_agent_skills.config_io`.

## 14. Testing strategy

| Layer | Approach | Approx. test count |
|---|---|---|
| `bars.py` | Decimal coercion, EMA correctness vs hand-calculated values | 8 |
| `pivots.py` | Synthetic OHLC sequences with known pivots; HH/HL classification | 12 |
| `structure.py` | S/R clustering, regime classification (trend / range / transition) | 10 |
| `fvg.py` | Three-bar textbook setups; filled / partially / unfilled | 8 |
| `order_block.py` | Displacement detection, retest tracking | 6 |
| `liquidity.py` | BSL/SSL detection, sweep state transitions | 6 |
| Per-detector | 3-5 fixtures each (positive + near-miss + edge), reusing hand-rolled blob factories | 9 × 4 = 36 |
| `scoring.py` | Fixed inputs → fixed outputs; weight sensitivity | 6 |
| `scan.py` | Full bundle → schema-compliant output | 6 |
| CLI roundtrip | JSON-in / JSON-out | 3 |
| Schema validation | Output always matches `schema.py` | 3 |
| Edge cases | sparse, stale, closed market, mtf_conflict, no_setup | 6 |
| **Total** | | **~110** |

In line with skill 4 (106) and skill 3 (124). All deterministic; no live MT5 required.

Fixture pattern: hand-rolled `_xauusd_bundle()` and helpers per timeframe, mirroring `_eurusd_blob()` style from existing tests.

## 15. v2 deferrals (out of scope for this design)

- **Inside-bar breakout detector**
- **Double top / double bottom detector**
- **CHoCH entry detector** (semantically overlaps BOS for v1 — will revisit if BOS proves insufficient)
- **Correlation overlay** for multi-symbol setup conflicts (composing with `pre-trade-checklist`'s exposure-overlap logic)
- **Image rendering** (matplotlib snapshot of detected structure for visual review — would require a new dependency and a separate `/render` command)
- **Backtesting harness** for tuning `quality_threshold` against historical bar windows
- **Tick-data refinement** for sub-bar entry triggers (mt5-mcp would need a tick-history tool first)

## 16. Conventions preserved

- Decimal-typed money/price/volume; reject floats at boundary.
- JSON-stdin → pure function → JSON-stdout.
- Hand-rolled fixture factories.
- Strict validation at write boundaries.
- Conventional commits, no `Co-Authored-By:` trailer.
- SKILL.md frontmatter follows existing pattern (description triggers + workflow steps + mt5-mcp tool list).

## 17. Acceptance criteria for v1

1. All 9 detectors produce structurally-valid candidates against curated fixtures (no false positives in negative-case tests).
2. Output schema validated by `schema.py` on every test invocation.
3. Empty `setups: []` is a first-class result; `warnings: ["no_clean_setup"]` when score threshold gates everything.
4. `pre-trade-checklist` and `position-sizer` run successfully when piped the selected setup's `{symbol, side, suggested_entry, invalidation, stop_distance}`.
5. `~110` pytest cases pass in ≤3s.
6. Live-broker smoke test on XAUUSD (swing mode) and NAS100 (intraday mode) returns non-empty structure and at least one viable narrative path.
7. Trigger phrases in §3 reliably activate the skill in the Claude Code agent.
