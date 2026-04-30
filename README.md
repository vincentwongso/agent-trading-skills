# trading-agent-skills

Reasoning-layer Claude Code skills for day trading on top of [`mt5-mcp`](https://github.com/vincentwongso/mt5-mcp) and [Calix](https://calix.fintrixmarkets.com).

Five skills (all shipped — start with whichever fits the moment):

1. **`position-sizer`** — Computes lot size for a target risk %, with broker-authoritative margin cross-check and swap-aware output.
2. **`daily-risk-guardian` + `pre-trade-checklist`** — Track today's P&L vs. configurable cap (NY 4pm ET reset), gate new trades against news / session / exposure / spread.
3. **`trade-journal`** — Append-only JSONL journal of completed trades with R-multiple, swap-accrued, and post-trade reflection.
4. **`session-news-brief`** — Dynamic watchlist + Calix calendar overlay + 3-API news fan-out + swing-candidates section (positive carry × technical extremes).
5. **`price-action`** — Hybrid classical + ICT structural reader. Per-TF pivots / S/R / FVGs / order blocks / liquidity pools / EMA stack / regime, plus 9 setup detectors (pullback-EMA, S/R bounce, pin bar, engulfing, range break+retest, FVG fill, OB retest, liq sweep, BOS pullback) ranked by deterministic structural quality score; LLM picks the chosen candidate and hands off to `pre-trade-checklist` + `position-sizer`.

None of the skills mutate broker state — they advise, gate, or record. All execution stays behind `mt5-trading`'s existing consent flow.

See [`trading-agent-skills-plan.md`](trading-agent-skills-plan.md) for the original design, and [`docs/superpowers/specs/`](docs/superpowers/specs/) for per-skill specs.

## Layout

```
src/trading_agent_skills/        # pure-Python helpers, Decimal-typed, no I/O at the package boundary
  decimal_io.py        # D() coercion (rejects floats), floor_to_step, quantize_price
  symbol_meta.py       # currencies-of-interest mapping, conversion-pair derivation
  margin_calc.py       # EnCalcMode dispatch (ported 1:1 from cfd-claculator)
  swap_calc.py         # daily swap per lot in deposit ccy + multi-night with 3x rollover
  position_sizer.py    # skill 1 orchestrator
  journal_io.py        # skill 2 schema-versioned write/read with strict validation
  journal_stats.py     # skill 2 analytics
  config_io.py         # skill 3 ~/.trading-agent-skills/config.toml read/write + defaults
  daily_state.py       # skill 3 NY 4pm ET reset bookkeeping (zoneinfo, DST-safe)
  risk_state.py        # skill 3 Position dataclass + AT_RISK / RISK_FREE / LOCKED_PROFIT
  guardian.py          # skill 3 daily-risk-guardian orchestrator (CLEAR/CAUTION/HALT)
  calix_client.py      # skill 3+4 Calix HTTPS client w/ 60s on-disk cache
  spread_baseline.py   # skill 3 EWMA per-symbol spread baseline
  checklist.py         # skill 3 pre-trade-checklist orchestrator (PASS/WARN/BLOCK)
  indicators.py        # skill 4 Wilder ATR/RSI + EMA on Decimal bars
  news_dedup.py        # skill 4 URL canonicalisation, Levenshtein dedup, impact classifier
  watchlist.py         # skill 4 5-tier resolver (explicit / positions / calendar / vol / default)
  news_clients.py      # skill 4 Finnhub / Marketaux / ForexNews httpx clients
  news_brief.py        # skill 4 session-news-brief orchestrator
  price_action/        # skill 5 sub-package
    bars.py            #   MTF wrapper around indicators.Bar
    pivots.py          #   fractal pivot detection + HH/HL/LH/LL classification
    structure.py       #   S/R clustering + regime + MTF alignment
    fvg.py             #   three-bar FVG detection with fill state
    order_block.py     #   OB detection + retest tracking
    liquidity.py       #   BSL/SSL pools + sweep state
    context.py         #   ScanContext composes per-TF derived structure
    scoring.py         #   deterministic structural quality score
    schema.py          #   ScanResult output schema
    scan.py            #   orchestrator wiring all 9 detectors
    detectors/         #   9 detector modules (one per setup type)
  cli/{size,journal,guardian,checklist,news,price_action}.py
.claude/skills/        # one folder per skill (SKILL.md + thin scripts/ entry points)
  position-sizer/SKILL.md
  trade-journal/SKILL.md
  daily-risk-guardian/SKILL.md
  pre-trade-checklist/SKILL.md
  session-news-brief/SKILL.md
  price-action/SKILL.md
tests/                 # pytest, no live broker required (~440+ cases)
```

Runtime files (not committed) live under `~/.trading-agent-skills/`: `journal.jsonl`, `config.toml`, `daily_state.json`, `spread_baseline.json`, `calix_cache/`, `news_cache/`.

## Quick start

```bash
python -m venv .venv
.venv/Scripts/activate    # Windows; `source .venv/bin/activate` elsewhere
pip install -e ".[dev]"
pytest                    # ~440 tests in ~1s
```

Each skill registers a CLI entry point:

| Skill | Entry point |
|---|---|
| `position-sizer` | `trading-agent-skills-size` |
| `trade-journal` | `trading-agent-skills-journal` |
| `daily-risk-guardian` | `trading-agent-skills-guardian` |
| `pre-trade-checklist` | `trading-agent-skills-checklist` |
| `session-news-brief` | `trading-agent-skills-news` |
| `price-action` | `trading-agent-skills-price-action` |

All CLIs read a JSON bundle from stdin (or `--input <file>`) and write JSON to stdout. The Claude Code agent fans out the relevant `mt5-mcp` tool calls, builds the bundle, pipes it in, and renders the result.

## Architecture in one paragraph

Skills don't make MCP calls. The agent (Claude Code) reads a `SKILL.md`, fans out MCP tool calls (`get_account_info`, `get_quote`, `get_symbols`, `calc_margin`, `get_history`, `get_positions`, `get_rates`), bundles outputs as JSON, pipes to `python -m trading_agent_skills.cli.<name>`, and renders the JSON result. The CLI calls a pure function in `src/trading_agent_skills/` that takes Decimal-typed inputs and returns a Decimal-typed result. Tests pass plain dicts through the same `from_mcp` constructors production code uses — no `unittest.mock`. mt5-mcp's contract is "Decimal as string"; this repo preserves that boundary throughout.

## API keys (skill 4 only)

The news fan-out reads keys from environment variables — never config or code:

| Provider | Env var |
|---|---|
| Finnhub | `FINNHUB_API_KEY` |
| Marketaux | `MARKETAUX_API_KEY` |
| ForexNews | `FOREXNEWS_API_KEY` |

A missing key produces a `MISSING_NEWS_API_KEY` flag and that provider is skipped — the brief still runs with the other two.

## Conventions

- **Decimal-typed money/price/volume everywhere**; reject floats at boundaries via `decimal_io.D()`.
- **JSON-stdin → pure function → JSON-stdout** for every skill's CLI surface.
- **Hand-rolled fixture factories** over mocks — schema drift fails loudly.
- **Conventional commits**, no `Co-Authored-By:` trailer.
- **Strict validation at write boundaries** (journal rejects naive datetimes, unknown enums, zero stop distance).
