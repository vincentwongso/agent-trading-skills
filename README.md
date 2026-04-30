# trading-agent-skills

> **AI agent installing this for a user?** → read [`AGENTS.md`](AGENTS.md) instead. It's a step-by-step playbook (detect harness → install Python pkg → register skills → verify) you can execute end-to-end. The rest of this README is human-oriented reference.

Reasoning-layer agent skills for day trading on top of [`mt5-mcp`](https://github.com/vincentwongso/mt5-mcp) (live MetaTrader 5 access) and [Calix](https://calix.fintrixmarkets.com) (economic + earnings calendar).

Six skills, all read-only / advisory — none of them mutate broker state. Execution stays behind your existing `mt5-trading` consent flow.

| # | Skill | What it does |
|---|---|---|
| 1 | **`position-sizer`** | Lot size for a target risk %, with broker-authoritative margin cross-check and swap-aware output. |
| 2 | **`trade-journal`** | Append-only JSONL of completed trades + R-multiple / swap-only P&L analytics. |
| 3 | **`daily-risk-guardian`** | Today's P&L vs. daily cap (NY 4pm ET reset). Worst-case drawdown across AT_RISK / RISK_FREE / LOCKED_PROFIT positions. |
| 4 | **`pre-trade-checklist`** | Composes guardian + Calix proximity + session + exposure + spread baseline → PASS / WARN / BLOCK. |
| 5 | **`session-news-brief`** | Dynamic watchlist + Calix overlay + 3-API news fan-out (Finnhub / Marketaux / ForexNews) + swing candidates. |
| 6 | **`price-action`** | Hybrid classical + ICT structural reader. 9 setup detectors; ranked candidates hand off to `pre-trade-checklist` + `position-sizer`. |

See [`trading-agent-skills-plan.md`](trading-agent-skills-plan.md) for the original design and [`docs/superpowers/specs/`](docs/superpowers/specs/) for per-skill specs.

---

## Prerequisites

Common to every skill:

- **Python 3.11+** (the package targets 3.11; the dev env uses 3.14).
- **A pip-installable copy of this repo** so the CLI entry points (`trading-agent-skills-size`, etc.) resolve on `PATH`. The skill `SKILL.md` files invoke them by name.

Per-skill extras:

| Skill | Needs `mt5-mcp` | Needs Calix | Needs news API keys |
|---|---|---|---|
| `position-sizer` | ✅ required | — | — |
| `trade-journal` | optional (auto-populate from `get_history`) | — | — |
| `daily-risk-guardian` | ✅ required | — | — |
| `pre-trade-checklist` | ✅ required | ✅ required | — |
| `session-news-brief` | ✅ required (positions / rates / symbols) | ✅ required | ✅ at least one of three |
| `price-action` | ✅ required | — | — |

`mt5-mcp` is a separate project — install it independently; this repo only consumes its tool outputs as JSON. See [`vincentwongso/mt5-mcp`](https://github.com/vincentwongso/mt5-mcp) for setup. The skills here are deliberately decoupled: the agent fans out MCP calls, bundles the results, and pipes them into a local CLI. No Python code in this repo imports an MCP client.

---

## 1. Install the Python package

```bash
git clone https://github.com/vincentwongso/agent-trading-skills.git
cd agent-trading-skills

python -m venv .venv
.venv/Scripts/activate         # Windows
# source .venv/bin/activate    # macOS / Linux

pip install -e ".[dev]"
pytest                         # ~440 tests, ~1s — confirms the install works
```

This registers six CLI entry points on `PATH`:

| Skill | Entry point |
|---|---|
| `position-sizer` | `trading-agent-skills-size` |
| `trade-journal` | `trading-agent-skills-journal` |
| `daily-risk-guardian` | `trading-agent-skills-guardian` |
| `pre-trade-checklist` | `trading-agent-skills-checklist` |
| `session-news-brief` | `trading-agent-skills-news` |
| `price-action` | `trading-agent-skills-price-action` |

Each CLI reads a JSON bundle from stdin (or `--input <file>`) and writes JSON to stdout. The skill `SKILL.md` files document the bundle shape per skill.

> ⚠ The agent must invoke these CLIs from a shell where the venv is on `PATH`. Either activate the venv before launching your harness, or install the package into a Python the harness's shell can see (e.g. `pipx install -e .` for a global install).

---

## 2. Install the skills into your agent harness

Each skill is a self-contained directory under `.claude/skills/<name>/` with this structure:

```
<name>/
  SKILL.md          # YAML frontmatter (name, description) + instructions
  scripts/<name>.py # thin shim that calls the entry point
```

The format follows the Anthropic Skills convention (frontmatter + markdown instructions + optional helper scripts). Most agent harnesses that support skills can load this directory directly.

### Claude Code

Claude Code auto-discovers skills from two locations:

| Scope | Path |
|---|---|
| Project | `<repo>/.claude/skills/<name>/` |
| User (global) | `~/.claude/skills/<name>/` |

**Project-scoped install** (only this repo can use the skills):

The skills already live at `.claude/skills/` in this repo — no copy needed. Open Claude Code in the project directory and the six skills will register automatically.

**User-scoped install** (every Claude Code session can use them):

```bash
# Windows (PowerShell)
$src = "C:\projects\agent-trading-skills\.claude\skills"
$dst = "$env:USERPROFILE\.claude\skills"
New-Item -ItemType Directory -Force $dst | Out-Null
Get-ChildItem $src -Directory | ForEach-Object {
  New-Item -ItemType SymbolicLink -Path "$dst\$($_.Name)" -Target $_.FullName -Force
}

# macOS / Linux
mkdir -p ~/.claude/skills
for d in .claude/skills/*/; do
  ln -sfn "$(pwd)/$d" ~/.claude/skills/"$(basename "$d")"
done
```

Symlinks are preferred over copies so the skills stay in sync with the repo as you `git pull`.

Verify with `/help` inside Claude Code — the six skills should appear in the available-skills list with their trigger descriptions.

### OpenClaw ([openclaw.ai](https://openclaw.ai))

OpenClaw uses the same `SKILL.md` format and scans these directories in priority order (first match wins):

| Scope | Path |
|---|---|
| Workspace (highest) | `<workspace>/skills/` |
| Workspace alt | `<workspace>/.agents/skills/` |
| User (cross-harness) | `~/.agents/skills/` |
| User (OpenClaw-only) | `~/.openclaw/skills/` |
| Bundled | (shipped with OpenClaw) |
| Custom (lowest) | paths from `skills.load.extraDirs` config |

Pick the `~/.agents/skills/` location if you want the same install to be picked up by other harnesses that follow the agentskills.io convention; pick `~/.openclaw/skills/` to keep it scoped to OpenClaw only.

```bash
# Windows (PowerShell) — installs into ~/.openclaw/skills/
$src = "C:\projects\agent-trading-skills\.claude\skills"
$dst = "$env:USERPROFILE\.openclaw\skills"
New-Item -ItemType Directory -Force $dst | Out-Null
Get-ChildItem $src -Directory | ForEach-Object {
  New-Item -ItemType SymbolicLink -Path "$dst\$($_.Name)" -Target $_.FullName -Force
}

# macOS / Linux
mkdir -p ~/.openclaw/skills
for d in .claude/skills/*/; do
  ln -sfn "$(pwd)/$d" ~/.openclaw/skills/"$(basename "$d")"
done
```

Verify with the OpenClaw skills CLI (`openclaw skills list` or via the Skill Workshop plugin) — the six skills should appear with their trigger descriptions.

### Hermes Agent ([nousresearch/hermes-agent](https://github.com/nousresearch/hermes-agent))

Hermes uses an enriched `SKILL.md` (the same Anthropic frontmatter, plus optional `version` / `platforms` / `metadata.hermes`). Plain Anthropic-format skills load fine — the extra fields are optional.

Skills live under `~/.hermes/skills/`, organised hierarchically by category. Use the bundled CLI to install:

```bash
# Install all six from this GitHub repo
hermes skills install vincentwongso/agent-trading-skills/.claude/skills/position-sizer
hermes skills install vincentwongso/agent-trading-skills/.claude/skills/trade-journal
hermes skills install vincentwongso/agent-trading-skills/.claude/skills/daily-risk-guardian
hermes skills install vincentwongso/agent-trading-skills/.claude/skills/pre-trade-checklist
hermes skills install vincentwongso/agent-trading-skills/.claude/skills/session-news-brief
hermes skills install vincentwongso/agent-trading-skills/.claude/skills/price-action
```

(Hermes's hub installer runs a security scan; the skills only shell out to local `trading-agent-skills-*` CLIs, no network calls except the news fan-out in `session-news-brief`.)

For a development setup that tracks `git pull` instead of taking a snapshot, symlink directly:

```bash
# macOS / Linux
mkdir -p ~/.hermes/skills/trading
for d in .claude/skills/*/; do
  ln -sfn "$(pwd)/$d" ~/.hermes/skills/trading/"$(basename "$d")"
done

# Windows (PowerShell)
$src = "C:\projects\agent-trading-skills\.claude\skills"
$dst = "$env:USERPROFILE\.hermes\skills\trading"
New-Item -ItemType Directory -Force $dst | Out-Null
Get-ChildItem $src -Directory | ForEach-Object {
  New-Item -ItemType SymbolicLink -Path "$dst\$($_.Name)" -Target $_.FullName -Force
}
```

If you operate Hermes in a team setup with a shared skills repo, add the path to `external_dirs` in `~/.hermes/config.yaml` instead — those entries are read-only but get picked up alongside `~/.hermes/skills/`.

### Other harnesses

If your harness doesn't auto-discover SKILL.md-format skills, install them as plain CLI tools:

1. Install the Python package as in step 1 (entry points on `PATH`).
2. Paste the relevant `SKILL.md` body into your harness's prompt / system instructions — they're written as standalone agent instructions.
3. Make sure your harness can shell out to the matching `trading-agent-skills-*` entry point and pipe a JSON bundle on stdin.

The bundle shapes are documented inside each `SKILL.md` and in `src/trading_agent_skills/cli/<name>.py`. Nothing in the JSON contract is harness-specific.

---

## 3. First-run setup

### Config file (`~/.trading-agent-skills/config.toml`)

The guardian / checklist / news skills read user-tunable defaults from this file. It's auto-generated on first invocation if missing:

```toml
[risk]
per_trade_max_pct = 1.0
daily_loss_cap_pct = 5.0
caution_threshold_pct = 2.5
concurrent_risk_budget_pct = 5.0

[watchlist]
default = ["XAUUSD", "XAGUSD", "USOIL", "UKOIL", "NAS100"]
base_universe = ["XAUUSD", "XAGUSD", "USOIL", "UKOIL", "NAS100", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD"]
max_size = 8
```

Edit to taste; change values are picked up on the next CLI invocation.

### News API keys (skill 5 only)

Set at least one of these as environment variables — never in `config.toml`, never in code:

| Provider | Env var | Get a key |
|---|---|---|
| Finnhub | `FINNHUB_API_KEY` | https://finnhub.io |
| Marketaux | `MARKETAUX_API_KEY` | https://marketaux.com |
| ForexNews | `FOREXNEWS_API_KEY` | https://forexnewsapi.com |

A missing key surfaces a `MISSING_NEWS_API_KEY` flag and that provider is skipped — the brief still runs on the others.

The news CLI also auto-loads a `.env` file. Search order:

1. `--env-file <path>` if passed explicitly
2. `~/.trading-agent-skills/.env` (preferred — survives across project locations)
3. `./.env` (repo-local override)

`.env.example` at the repo root is committed; `.env` is gitignored. PowerShell users prefer this over bash-only `export`. Real shell env wins over `.env` (we use `os.environ.setdefault`).

### Runtime files

All skills write to `~/.trading-agent-skills/` (created on first run):

```
~/.trading-agent-skills/
  journal.jsonl        # trade journal — append-only
  config.toml          # config; auto-defaults if missing
  daily_state.json     # NY-close session bookkeeping (guardian)
  spread_baseline.json # EWMA per-symbol spread baselines (checklist)
  calix_cache/         # 60s on-disk Calix cache
  news_cache/          # 60s on-disk news cache
  .env                 # optional, news API keys
```

None of these are committed to the repo.

---

## 4. Quick smoke test

Once installed, talk to your agent in plain English. With Claude Code:

```
> what lot size for XAUUSD with 1% risk and stop at 2680?
> show me the daily guardian
> can I take a long on UKOIL?
> morning brief
> what's the setup on NAS100
> journal my last UKOIL trade
```

The agent should match each phrase to a skill, fan out the relevant `mcp__mt5-mcp__*` calls, pipe the JSON bundle to the matching `trading-agent-skills-*` CLI, and render the result.

---

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
  price_action/        # skill 5 sub-package (bars, pivots, structure, fvg, order_block,
                       #   liquidity, context, scoring, schema, scan, detectors/)
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

---

## Architecture in one paragraph

Skills don't make MCP calls. The agent (Claude Code, OpenClaw, Hermes, etc.) reads a `SKILL.md`, fans out MCP tool calls (`get_account_info`, `get_quote`, `get_symbols`, `calc_margin`, `get_history`, `get_positions`, `get_rates`), bundles outputs as JSON, pipes to `python -m trading_agent_skills.cli.<name>`, and renders the JSON result. The CLI calls a pure function in `src/trading_agent_skills/` that takes Decimal-typed inputs and returns a Decimal-typed result. Tests pass plain dicts through the same `from_mcp` constructors production code uses — no `unittest.mock`. mt5-mcp's contract is "Decimal as string"; this repo preserves that boundary throughout.

---

## Conventions

- **Decimal-typed money/price/volume everywhere**; reject floats at boundaries via `decimal_io.D()`.
- **JSON-stdin → pure function → JSON-stdout** for every skill's CLI surface.
- **Hand-rolled fixture factories** over mocks — schema drift fails loudly.
- **Conventional commits**, no `Co-Authored-By:` trailer.
- **Strict validation at write boundaries** (journal rejects naive datetimes, unknown enums, zero stop distance).
