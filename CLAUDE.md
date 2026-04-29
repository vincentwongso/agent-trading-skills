# agent-trading-skills — handover notes

Reasoning-layer Claude Code skills for CFD day trading. Composes [`mt5-mcp`](https://github.com/vincentwongso/mt5-mcp) (local broker access via MetaTrader 5) with [Calix](https://calix.fintrixmarkets.com) (economic + earnings calendar) and three news APIs (Finnhub / Marketaux / ForexNews).

The canonical design is `cfd-trading-skills-plan.md` — read it before making any architectural decision. Persistent context (build state, user trading setup, conventions) lives in `~/.claude/projects/C--projects-cfd-trading-skills/memory/` — read MEMORY.md for the index.

## Status (last updated 2026-04-29)

Three of four skill bundles shipped on `main`:
- ✅ `cfd-position-sizer` — lot sizing + margin cross-check + swap-aware output
- ✅ `trade-journal` — append-only JSONL with R-multiple, swap-only P&L, swing-trade lens
- ✅ `daily-risk-guardian` + `pre-trade-checklist` (paired) — NY-close session reset, LLM-judged AT_RISK predicate, Calix proximity, EWMA spread baseline
- ⏳ `session-news-brief` (plan estimate 8–12h)

261 pytest cases passing in ~0.7s. Repo published to `git@github.com:vincentwongso/agent-trading-skills.git`.

## Architecture in one paragraph

Skills don't make MCP calls. The agent (Claude Code) reads a SKILL.md, fans out MCP tool calls (`get_account_info`, `get_quote`, `get_symbols`, `calc_margin`, `get_history`, `get_positions`, `get_rates`), bundles outputs as JSON, pipes to `python -m cfd_skills.cli.<name>`, and renders the JSON result. The CLI calls a pure function in `src/cfd_skills/` that takes Decimal-typed inputs and returns a Decimal-typed result. Tests pass plain dicts through the same `from_mcp` constructors production code uses — no `unittest.mock`.

Decimal handling is strict: `decimal_io.D()` rejects floats at runtime; CLI serialises via `format(d, "f")` to avoid scientific notation. mt5-mcp's contract is "Decimal as string"; this repo preserves that boundary.

## Layout

```
src/cfd_skills/        # pure-Python, Decimal-typed, no I/O at the package boundary
  decimal_io.py        # D() coercion (rejects floats), floor_to_step, quantize_price
  symbol_meta.py       # currencies-of-interest mapping, conversion-pair derivation
  margin_calc.py       # EnCalcMode dispatch (ported 1:1 from cfd-claculator)
  swap_calc.py         # daily swap per lot in deposit ccy + multi-night with 3x rollover
  position_sizer.py    # skill 1 orchestrator
  journal_io.py        # skill 2 schema-versioned write/read with strict validation
  journal_stats.py     # skill 2 analytics
  config_io.py         # skill 3 ~/.cfd-skills/config.toml read/write + defaults
  daily_state.py       # skill 3 NY 4pm ET reset bookkeeping (zoneinfo, DST-safe)
  risk_state.py        # skill 3 Position dataclass + AT_RISK / RISK_FREE / LOCKED_PROFIT
  guardian.py          # skill 3 daily-risk-guardian orchestrator (CLEAR/CAUTION/HALT)
  calix_client.py      # skill 3 Calix HTTPS client w/ 60s on-disk cache
  spread_baseline.py   # skill 3 EWMA per-symbol spread baseline
  checklist.py         # skill 3 pre-trade-checklist orchestrator (PASS/WARN/BLOCK)
  cli/{size,journal,guardian,checklist}.py
.claude/skills/
  cfd-position-sizer/SKILL.md + scripts/size.py
  trade-journal/SKILL.md + scripts/journal.py
  daily-risk-guardian/SKILL.md + scripts/guardian.py
  pre-trade-checklist/SKILL.md + scripts/checklist.py
tests/                 # pytest, no live broker required
~/.cfd-skills/         # runtime files (not committed):
  journal.jsonl        # trade journal
  config.toml          # config; auto-defaults if missing
  daily_state.json     # NY-close session bookkeeping
  spread_baseline.json # EWMA per-symbol spread baselines
  calix_cache/         # 60s on-disk Calix cache
  news_cache/          # skill 4 (not yet built)
```

## Quick commands

```bash
# Setup (Python 3.14 venv already exists at .venv/)
./.venv/Scripts/python.exe -m pip install -e ".[dev]"

# Tests
./.venv/Scripts/python.exe -m pytest tests/ -q

# Smoke-test position sizer
echo '<bundle>' | python -m cfd_skills.cli.size

# Smoke-test journal
echo '<entry>' | cfd-skills-journal write --json
cfd-skills-journal stats --group-by all

# Smoke-test guardian + checklist
echo '<bundle>' | cfd-skills-guardian
echo '<bundle>' | cfd-skills-checklist
```

## Conventions to preserve

- **Conventional commit messages**, no `Co-Authored-By:` trailer (matches existing history in mt5-mcp and this repo).
- **Decimal-typed money/price/volume everywhere**; reject floats at boundaries.
- **JSON-stdin → pure function → JSON-stdout** for every skill's CLI surface. Skills don't import MCP libs.
- **Hand-rolled fixture factories** (e.g. `_eurusd_blob()`) over mocks — schema drift fails loudly.
- **Test fixtures parity** with cfd-claculator's `margin.test.ts` for any margin-related changes.
- **Strict validation at write boundaries** (journal rejects naive datetimes, unknown enums, zero stop distance — bad data here poisons every retrospective query downstream).

## Resuming

Next skill is `session-news-brief` (skill 4). Plan section "Skill 4 — session-news-brief" in `cfd-trading-skills-plan.md` covers the design: Calix calendar+earnings + 3-API news fan-out (Finnhub / Marketaux / ForexNews) + ATR/RSI swing-candidates section + dynamic 5-tier watchlist resolver. Estimated 8–12h.

User's stated trading defaults (locked in 2026-04-29; see memory `project_user_trading_setup.md`):
- 1% per-trade max, 5% daily cap, 50% caution threshold
- 5% concurrent risk budget; risk-free positions don't count
- Default watchlist: XAUUSD, XAGUSD, USOIL, UKOIL, NAS100
- Swing style: positive-carry plays (UKOIL +$125/lot/night), trail SL to breakeven then lock profit

User said "I will test everything all in one at the end" — no live broker smoke test scheduled until all four skills land.

---

<!-- rtk-instructions v2 -->
# RTK (Rust Token Killer) - Token-Optimized Commands

## Golden Rule

**Always prefix commands with `rtk`**. If RTK has a dedicated filter, it uses it. If not, it passes through unchanged. This means RTK is always safe to use.

**Important**: Even in command chains with `&&`, use `rtk`:
```bash
# ❌ Wrong
git add . && git commit -m "msg" && git push

# ✅ Correct
rtk git add . && rtk git commit -m "msg" && rtk git push
```

## RTK Commands by Workflow

### Build & Compile (80-90% savings)
```bash
rtk cargo build         # Cargo build output
rtk cargo check         # Cargo check output
rtk cargo clippy        # Clippy warnings grouped by file (80%)
rtk tsc                 # TypeScript errors grouped by file/code (83%)
rtk lint                # ESLint/Biome violations grouped (84%)
rtk prettier --check    # Files needing format only (70%)
rtk next build          # Next.js build with route metrics (87%)
```

### Test (90-99% savings)
```bash
rtk cargo test          # Cargo test failures only (90%)
rtk vitest run          # Vitest failures only (99.5%)
rtk playwright test     # Playwright failures only (94%)
rtk test <cmd>          # Generic test wrapper - failures only
```

### Git (59-80% savings)
```bash
rtk git status          # Compact status
rtk git log             # Compact log (works with all git flags)
rtk git diff            # Compact diff (80%)
rtk git show            # Compact show (80%)
rtk git add             # Ultra-compact confirmations (59%)
rtk git commit          # Ultra-compact confirmations (59%)
rtk git push            # Ultra-compact confirmations
rtk git pull            # Ultra-compact confirmations
rtk git branch          # Compact branch list
rtk git fetch           # Compact fetch
rtk git stash           # Compact stash
rtk git worktree        # Compact worktree
```

Note: Git passthrough works for ALL subcommands, even those not explicitly listed.

### GitHub (26-87% savings)
```bash
rtk gh pr view <num>    # Compact PR view (87%)
rtk gh pr checks        # Compact PR checks (79%)
rtk gh run list         # Compact workflow runs (82%)
rtk gh issue list       # Compact issue list (80%)
rtk gh api              # Compact API responses (26%)
```

### JavaScript/TypeScript Tooling (70-90% savings)
```bash
rtk pnpm list           # Compact dependency tree (70%)
rtk pnpm outdated       # Compact outdated packages (80%)
rtk pnpm install        # Compact install output (90%)
rtk npm run <script>    # Compact npm script output
rtk npx <cmd>           # Compact npx command output
rtk prisma              # Prisma without ASCII art (88%)
```

### Files & Search (60-75% savings)
```bash
rtk ls <path>           # Tree format, compact (65%)
rtk read <file>         # Code reading with filtering (60%)
rtk grep <pattern>      # Search grouped by file (75%)
rtk find <pattern>      # Find grouped by directory (70%)
```

### Analysis & Debug (70-90% savings)
```bash
rtk err <cmd>           # Filter errors only from any command
rtk log <file>          # Deduplicated logs with counts
rtk json <file>         # JSON structure without values
rtk deps                # Dependency overview
rtk env                 # Environment variables compact
rtk summary <cmd>       # Smart summary of command output
rtk diff                # Ultra-compact diffs
```

### Infrastructure (85% savings)
```bash
rtk docker ps           # Compact container list
rtk docker images       # Compact image list
rtk docker logs <c>     # Deduplicated logs
rtk kubectl get         # Compact resource list
rtk kubectl logs        # Deduplicated pod logs
```

### Network (65-70% savings)
```bash
rtk curl <url>          # Compact HTTP responses (70%)
rtk wget <url>          # Compact download output (65%)
```

### Meta Commands
```bash
rtk gain                # View token savings statistics
rtk gain --history      # View command history with savings
rtk discover            # Analyze Claude Code sessions for missed RTK usage
rtk proxy <cmd>         # Run command without filtering (for debugging)
rtk init                # Add RTK instructions to CLAUDE.md
rtk init --global       # Add RTK to ~/.claude/CLAUDE.md
```

## Token Savings Overview

| Category | Commands | Typical Savings |
|----------|----------|-----------------|
| Tests | vitest, playwright, cargo test | 90-99% |
| Build | next, tsc, lint, prettier | 70-87% |
| Git | status, log, diff, add, commit | 59-80% |
| GitHub | gh pr, gh run, gh issue | 26-87% |
| Package Managers | pnpm, npm, npx | 70-90% |
| Files | ls, read, grep, find | 60-75% |
| Infrastructure | docker, kubectl | 85% |
| Network | curl, wget | 65-70% |

Overall average: **60-90% token reduction** on common development operations.
<!-- /rtk-instructions -->
