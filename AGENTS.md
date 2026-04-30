# AGENTS.md — install playbook for AI agents

> **If you are a human reader, you don't need this file** — go to [`README.md`](README.md). This file is the playbook an AI agent (Claude Code, OpenClaw, Hermes, etc.) follows when a user says *"install the trading-agent-skills from this repo for me."*

You are an AI agent. The user has access to two public repos and nothing else:

- this repo (`agent-trading-skills`)
- [`mt5-mcp`](https://github.com/vincentwongso/mt5-mcp) (separate prerequisite)

The user has asked you to install the skills. Follow this playbook end-to-end. Don't ask the user to read documentation themselves — execute each step, surface progress, and only ask the user when you need information you can't infer.

---

## Step 0 — Detect the host harness

Identify which agent harness you're running in. The right install path differs by harness.

| Signal | Likely harness | Skill install root |
|---|---|---|
| You can call `mcp__*` tools, see `.claude/skills/` already loaded, or your environment has `~/.claude/` | **Claude Code** | `~/.claude/skills/<name>/` |
| `~/.openclaw/` exists, or you have access to `openclaw` CLI / Skill Workshop plugin | **OpenClaw** | `~/.openclaw/skills/<name>/` (or `~/.agents/skills/<name>/` for cross-harness) |
| `~/.hermes/` exists, or you have access to `hermes` CLI | **Hermes Agent** | `~/.hermes/skills/trading/<name>/` |

If unsure, ask the user once:
> "Which agent harness am I running in — Claude Code, OpenClaw, or Hermes? I need to know which directory to install the skills into."

---

## Step 1 — Clone (or locate) this repo

If the user already has the repo cloned locally, ask them for the path. Otherwise clone it:

```bash
git clone https://github.com/vincentwongso/agent-trading-skills.git ~/agent-trading-skills
cd ~/agent-trading-skills
```

Replace `~/agent-trading-skills` with whatever local path the user prefers. **Remember this path** — every subsequent step references it. Call it `$REPO` from here on.

---

## Step 2 — Install the Python package

The skills are thin shims over a Python CLI; the CLI must be on the user's `PATH`.

Pick one approach, in order of preference:

### 2a. `pipx` (recommended — global, isolated, no venv-management for the user)

```bash
pipx install -e $REPO
```

Verify all six entry points are on PATH:

```bash
trading-agent-skills-size --help
trading-agent-skills-journal --help
trading-agent-skills-guardian --help
trading-agent-skills-checklist --help
trading-agent-skills-news --help
trading-agent-skills-price-action --help
```

If any are missing, fall through to 2b.

### 2b. venv inside the repo (fallback)

```bash
cd $REPO
python -m venv .venv
.venv/Scripts/activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -e ".[dev]"
pytest                          # ~440 tests, ~1s — confirms install works
```

⚠ With venv, the harness's shell must have the venv on PATH every time the skills run. If the user runs the harness from a different shell, the entry points won't resolve. **Tell the user this explicitly** and recommend `pipx` if they hit it.

### 2c. Already installed?

If the entry points are already on PATH, skip ahead to step 3 — don't reinstall.

---

## Step 3 — Register the skills with the harness

### Claude Code

```bash
# macOS / Linux
mkdir -p ~/.claude/skills
for d in $REPO/.claude/skills/*/; do
  ln -sfn "$d" ~/.claude/skills/"$(basename "$d")"
done

# Windows (PowerShell)
$src = "$REPO\.claude\skills"
$dst = "$env:USERPROFILE\.claude\skills"
New-Item -ItemType Directory -Force $dst | Out-Null
Get-ChildItem $src -Directory | ForEach-Object {
  New-Item -ItemType SymbolicLink -Path "$dst\$($_.Name)" -Target $_.FullName -Force
}
```

Verify by reloading the harness — the six skills should appear in the available-skills list.

### OpenClaw

```bash
# macOS / Linux
mkdir -p ~/.openclaw/skills
for d in $REPO/.claude/skills/*/; do
  ln -sfn "$d" ~/.openclaw/skills/"$(basename "$d")"
done

# Windows (PowerShell)
$src = "$REPO\.claude\skills"
$dst = "$env:USERPROFILE\.openclaw\skills"
New-Item -ItemType Directory -Force $dst | Out-Null
Get-ChildItem $src -Directory | ForEach-Object {
  New-Item -ItemType SymbolicLink -Path "$dst\$($_.Name)" -Target $_.FullName -Force
}
```

Verify with `openclaw skills list` (or via the Skill Workshop plugin).

### Hermes Agent

If `hermes skills install` is available, use it (runs a security scan):

```bash
hermes skills install vincentwongso/agent-trading-skills/.claude/skills/position-sizer
hermes skills install vincentwongso/agent-trading-skills/.claude/skills/trade-journal
hermes skills install vincentwongso/agent-trading-skills/.claude/skills/daily-risk-guardian
hermes skills install vincentwongso/agent-trading-skills/.claude/skills/pre-trade-checklist
hermes skills install vincentwongso/agent-trading-skills/.claude/skills/session-news-brief
hermes skills install vincentwongso/agent-trading-skills/.claude/skills/price-action
```

For a dev setup that tracks `git pull`, symlink instead:

```bash
# macOS / Linux
mkdir -p ~/.hermes/skills/trading
for d in $REPO/.claude/skills/*/; do
  ln -sfn "$d" ~/.hermes/skills/trading/"$(basename "$d")"
done

# Windows (PowerShell)
$src = "$REPO\.claude\skills"
$dst = "$env:USERPROFILE\.hermes\skills\trading"
New-Item -ItemType Directory -Force $dst | Out-Null
Get-ChildItem $src -Directory | ForEach-Object {
  New-Item -ItemType SymbolicLink -Path "$dst\$($_.Name)" -Target $_.FullName -Force
}
```

Verify with `hermes skills list`.

---

## Step 4 — Check `mt5-mcp` is set up

Five of the six skills need `mt5-mcp` (only `trade-journal` works fully without it). Try:

```
mcp__mt5-mcp__ping
```

If the tool isn't available or returns an error, the user hasn't set up `mt5-mcp` yet. Tell them:

> "Five of the six skills (everything except `trade-journal`) need the `mt5-mcp` server connected to your MetaTrader 5 terminal. Install it from https://github.com/vincentwongso/mt5-mcp and register it with your harness's MCP config. I can continue installing the skills now — they'll work once mt5-mcp is up."

Don't block install on this — the skills install fine without mt5-mcp, they just can't fetch live broker data until it's connected.

---

## Step 5 — Walk the user through optional config

The skills work with safe defaults out of the box. But two things are worth a one-time conversation:

### 5a. Risk defaults — `~/.trading-agent-skills/config.toml`

Auto-generated on first invocation of any guardian/checklist/news skill. Defaults:

- `risk.per_trade_max_pct = 1.0`
- `risk.daily_loss_cap_pct = 5.0`
- `risk.caution_threshold_pct = 2.5`
- `risk.concurrent_risk_budget_pct = 5.0`
- `watchlist.default = ["XAUUSD", "XAGUSD", "USOIL", "UKOIL", "NAS100"]`

Ask the user once:

> "The skills default to 1% risk per trade and a 5% daily loss cap. Want to change either of these? They'll go in `~/.trading-agent-skills/config.toml`."

### 5b. News API keys — `session-news-brief` only

Without at least one of these, the news section of the brief is empty. Ask:

> "The `session-news-brief` skill fans out to three news APIs — all have free tiers. Set at least one to populate the news section:
>
> - Finnhub: https://finnhub.io
> - Marketaux: https://marketaux.com
> - ForexNews: https://forexnewsapi.com
>
> Once you've signed up for one or more, paste the key(s) and I'll write them to `~/.trading-agent-skills/.env` (the CLI auto-loads it). Skip if you only want the calendar + swing-candidates sections."

When the user provides keys, write them to `~/.trading-agent-skills/.env`:

```
FINNHUB_API_KEY=...
MARKETAUX_API_KEY=...
FOREXNEWS_API_KEY=...
```

Use `os.environ.setdefault` semantics — real shell env wins, so this file is the persistent default.

---

## Step 6 — Verify install with one smoke test per skill

Once the harness has reloaded the skills, run a smoke test by talking to the user normally. Use these prompts (or paraphrase):

| Skill | Verification prompt |
|---|---|
| `position-sizer` | "What lot size for XAUUSD with 1% risk and a 200-point stop?" |
| `trade-journal` | "Show me my trade journal for this week." (Empty journal returns `count: 0` — that's success.) |
| `daily-risk-guardian` | "Show me the daily guardian." |
| `pre-trade-checklist` | "Pre-trade check for XAUUSD long." |
| `session-news-brief` | "Morning brief." |
| `price-action` | "What's the setup on XAUUSD?" |

If a skill returns a `command not found` error, the Python entry point isn't on PATH for the harness's shell — go back to step 2.

If a skill returns an MCP error like "tool not available", `mt5-mcp` isn't connected — go to step 4.

---

## Done

Tell the user:

> "Trading skills installed. The full setup (keys, config, mt5-mcp) is documented in [README.md](README.md) if you want to read it later, but you don't need to — each skill will guide you through any missing config the first time you use it."

Don't dump documentation links unprompted. The user came to you to avoid reading docs.

---

## Setting up autonomous trading

This section is fired when the user says any of: "set up autonomous trading",
"configure the trading agent", "initialize charter", "I want the agent to
trade my demo account."

### 1. Confirm prerequisites

- mt5-mcp connected (`mcp__mt5-mcp__ping` succeeds).
- Skills installed (per top of this file).
- Demo account exists in MT5 terminal.

### 2. Walk the user through the charter Q&A

Ask the following questions, one at a time. Use sensible defaults; offer to
re-prompt if the answer is out of range. After all questions, write the
charter to `~/.trading-agent-skills/accounts/<account_id>/charter.md`.

**Hard fields (required):**

1. "What's the MT5 demo account number?" (must match a demo login the user
   can verify via `mcp__mt5-mcp__get_account_info`)

2. (If broker reachable) "Confirming via get_account_info... balance is
   <X> <CCY>, server is <SERVER>. Correct account?" If broker not
   reachable, skip this and note that first heartbeat tick will validate.

3. "What's your trading style? scalp / day / swing?" Defaults map to:
    - scalp → 15m heartbeat, allowed range 5m-15m
    - day   → 1h heartbeat, allowed range 30m-1h
    - swing → 4h heartbeat, allowed range 1h-4h

4. "Heartbeat? Default <X> for your style. Override?"

5. "Per-trade risk cap (% of equity)? Default 1.0%, max 5.0%."

6. "Daily loss cap (% of equity)? Default 5.0%, max 20.0%."

7. "Max concurrent positions? Default 3."

**Soft fields (optional — ask the user if they want to constrain):**

> "That covers the hard rules. Want to constrain instruments, sessions, or
> setup types — or leave it open and let the agent decide each tick?"

If the user volunteers constraints, fill the relevant fields. If not, leave
empty (the agent will use the 5-tier resolver, all sessions, all setup types).

### 3. Write the charter

Render the YAML and write to disk:

```bash
mkdir -p ~/.trading-agent-skills/accounts/<account_id>/charter_versions
mkdir -p ~/.trading-agent-skills/accounts/<account_id>/proposals
cat > ~/.trading-agent-skills/accounts/<account_id>/charter.md << 'EOF'
mode: demo
account_id: <account_id>
heartbeat: <heartbeat>
hard_caps:
  per_trade_risk_pct: <pct>
  daily_loss_pct: <pct>
  max_concurrent_positions: <n>
charter_version: 1
created_at: <ISO 8601 with offset, e.g. 2026-04-30T14:00:00+10:00>
created_account_balance: <balance>
trading_style: <scalp|day|swing>
sessions_allowed: []
instruments: []
allowed_setups: []
notes: ""
EOF
```

### 4. Confirm the heartbeat trigger

After charter is written, instruct the user how to start the heartbeat:

- Claude Code: `/loop <heartbeat> /trading-heartbeat`
- OpenClaw: add a cron entry pointing to the trading-heartbeat skill at the
  configured cadence.
- Hermes: add a heartbeat-system entry pointing to the trading-heartbeat skill.

### 5. Smoke test

Fire one manual tick and check that a decision record (likely a
`weekend` / `all_markets_closed` / `guardian_clear` skip) appears in
`~/.trading-agent-skills/accounts/<account_id>/decisions.jsonl`.

---

## Troubleshooting

- **`pip install -e .` fails on Python < 3.11** — the package requires 3.11+. Tell the user to upgrade Python.
- **`tzdata` import fails on Windows** — the `tzdata` package is declared as a Windows-only dep in `pyproject.toml`. If it didn't install, run `pip install tzdata` manually.
- **`pytest` finds 0 tests** — you ran it from outside the repo root. `cd $REPO` first.
- **Harness doesn't see the skills after symlinking** — most harnesses need a reload (Claude Code: `/restart` or new session; OpenClaw: `openclaw skills reload`; Hermes: `hermes skills list` to refresh). If still missing, check the symlink target with `ls -la ~/.<harness>/skills/`.
- **`hermes skills install` rejects the path** — make sure the target is the skill *directory* (containing `SKILL.md`), not the `SKILL.md` file itself.
