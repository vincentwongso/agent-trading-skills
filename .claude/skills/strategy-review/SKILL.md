---
name: strategy-review
description: Use when the user asks for a weekly strategy review, performance analysis, or wants to refine the autonomous-trading charter based on recent results. Triggers on phrases like "weekly review", "strategy review", "how did this week go for the agent", "should we tweak the charter". Reads journal + decision-log + spread baselines for the active account, builds a markdown proposal with structural recommendations, asks the user which changes to apply, then bumps the charter version. Never auto-applies. Cannot propose changes to mode (demo↔live) or account_id — those are user-initiated only.
---

# strategy-review — weekly retrospective + charter tuning

This skill produces a strategy-review proposal at the end of each week and
walks the user through approve/reject decisions. The charter only changes
after explicit user approval.

## Prerequisites

- Charter exists at `~/.trading-agent-skills/accounts/<account_id>/charter.md`
  (created via the install Q&A in AGENTS.md).
- Journal has at least one closed trade for the window (otherwise the proposal
  will mostly say "no data, no changes recommended").

## Trigger

- Claude Code: `/strategy-review` (manual) or schedule weekly via `/schedule`.
- OpenClaw / Hermes: cron entry on Sunday evening.

## Workflow

### 1. Resolve account context

```bash
ACCOUNT_ID=$(ls ~/.trading-agent-skills/accounts/ | head -1)
# Or use $TRADING_AGENT_ACCOUNT_ID if set.
```

### 2. Build the proposal skeleton

```bash
trading-agent-skills-strategy-review propose \
  --account-id "$ACCOUNT_ID" \
  --since "$(date -u -d '7 days ago' +%FT%TZ)" \
  --until "$(date -u +%FT%TZ)"
```

This writes `~/.trading-agent-skills/accounts/$ACCOUNT_ID/proposals/<date>.md`.
The skeleton has Python-aggregated facts and **placeholder slots** for the
LLM to fill: a YAML diff fence + reasoning section.

### 3. Fill the proposal

Read the skeleton. For each section:

- **Performance summary** — already filled by Python.
- **Setup-type breakdown** — already filled.
- **Decision-log analysis** — already filled.
- **Charter diff proposal** — fill the ` ```diff` fence with concrete proposed
  changes based on the stats. Only use fields in this list:

  Proposable: `per_trade_risk_pct`, `daily_loss_pct`, `max_concurrent_positions`,
  `heartbeat`, `trading_style`, `sessions_allowed`, `instruments`,
  `allowed_setups`, `notes`.

  Forbidden (locked): `mode`, `account_id`, `created_at`,
  `created_account_balance`, `charter_version`. NEVER propose changes to these.

- **Reasoning** — fill with 1-2 sentences per proposed change explaining why.

### 4. Present to the user

Show the user the proposal text. Ask:

> "Reply with: `approve all` / `approve <fields>` / `reject` / `discuss <topic>`."

### 5. Apply approved changes

If user replies with `approve all` or `approve <fields>`, build a JSON object
with ONLY those fields and pipe to apply:

```bash
echo '{"per_trade_risk_pct": 0.8, "allowed_setups": ["price_action:pin_bar"]}' \
  | trading-agent-skills-strategy-review apply --account-id "$ACCOUNT_ID"
```

The CLI:
- Validates no locked fields.
- Bumps `charter_version`.
- Archives the prior charter to `charter_versions/v<N>.md`.
- Writes the new charter.

If user replies with `reject` or `discuss`, do NOT call apply. Proposal file
is preserved either way for the audit trail.

## Hard rules

- NEVER propose mode changes (demo↔live). The proposal generator's diff
  validator will refuse, but you should not even include them in the diff.
- NEVER apply changes without an explicit `approve` reply. "Sounds good"
  is not approval — ask for the exact form.
- NEVER mutate journal or decision-log files. This skill is read-only on those.

## Out of scope

- Mode flip (demo→live) — see AGENTS.md "Demo→live runbook".
- Trade execution — see trading-heartbeat skill.
- Manual journal edits — see trade-journal skill.
