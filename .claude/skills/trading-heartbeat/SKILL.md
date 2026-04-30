---
name: trading-heartbeat
description: Use to run one autonomous trading cycle (a "tick") on a configured demo MT5 account. Triggered by the harness on a recurring schedule matching the charter's heartbeat (15m / 1h / 4h via /loop, OpenClaw cron, or Hermes heartbeat). Each tick reads the operating charter, checks kill conditions (guardian HALT, market closed, broker unreachable), manages open positions (close/modify based on structural re-evaluation), and scans for new entries through pre-trade-checklist + position-sizer. Every action — and every evaluated-but-skipped candidate — is logged to the decision log with reasoning. Trade execution uses mt5-mcp (place_order / close_position / modify_order). Read-write to the demo account; never operates on live mode unless charter.mode is explicitly "live".
---

# trading-heartbeat — autonomous tick

This skill executes ONE heartbeat tick. It is fired by:

- Claude Code: `/loop <heartbeat> /trading-heartbeat`
- OpenClaw: internal cron entry pointing to this skill
- Hermes: heartbeat system entry pointing to this skill

## Prerequisites (first-run)

Before any tick can run:

1. **mt5-mcp connected** — `mcp__mt5-mcp__ping` must succeed. If not, run mt5-mcp install per AGENTS.md.
2. **Charter exists** at `~/.trading-agent-skills/accounts/<account_id>/charter.md`. If not, walk the user through install (AGENTS.md "Setting up autonomous trading" section).
3. **Account context resolved**. Use the `TRADING_AGENT_ACCOUNT_ID` env var if set; else the most-recently-modified `accounts/<id>/` directory.

## Tick cycle (deterministic)

Read the spec at `docs/superpowers/specs/2026-04-30-autonomous-trading-loop-design.md` §7.2 for the canonical cycle. Summary:

### 1. Bootstrap

- Resolve `account_id` (env or single account dir).
- Load charter via:
  ```bash
  cat ~/.trading-agent-skills/accounts/<account_id>/charter.md
  ```
  Parse mode, heartbeat, hard_caps, soft fields. If charter unparseable, log a `skip` decision with reasoning="charter_invalid" and exit.
- Compute `tick_id = current UTC ISO 8601 timestamp` (e.g. `2026-04-30T22:00:00+00:00`).

### 2. Verify broker

- Call `mcp__mt5-mcp__get_account_info`.
- If `account_info.login != charter.account_id` → write skip decision (kind=skip, symbol="*", reasoning="account_mismatch: broker reports <X>, charter says <Y>"). Exit.
- If broker unreachable / errors → write skip with reasoning="broker_unreachable: <error>". Exit.

### 3. Kill conditions

Run in order; first hit exits the tick.

- **Mode check.** If charter.mode != "demo" AND != "live" → skip "invalid_mode". Exit.
- **Guardian.** Build the daily-risk-guardian bundle and pipe through `trading-agent-skills-guardian`. If status=="HALT" → skip "guardian_halt". Exit.
- **Sessions.** If charter.sessions_allowed is non-empty AND current session not in list → skip "session_closed".
- **Markets.** For each instrument in resolved instrument list, call `mcp__mt5-mcp__get_market_hours`. If ALL closed → skip "all_markets_closed". Exit.

### 4. Manage open positions

- Call `mcp__mt5-mcp__get_positions`.
- For each position:
  - Run `trading-agent-skills-price-action` on the position's primary timeframe.
  - Reasoning to act:
    - **Structural invalidation** (level broken in opposite direction): close position. Write decision intent (kind=close), call `mcp__mt5-mcp__close_position`, write outcome.
    - **TP near**: hold (no log). Let the broker fill TP naturally.
    - **SL trail warranted** (e.g., new HTF level above original SL): modify. Decision intent (kind=modify), `mcp__mt5-mcp__modify_order`, outcome.
    - **No change warranted**: hold silently — no decision log.

### 5. Scan for new entries

- Resolve instrument list:
  - If `charter.instruments` non-empty → use it.
  - Else → invoke `trading-agent-skills-news` and extract the resolved watchlist from its output (the news brief surfaces the 5-tier resolved symbols at `output.watchlist.symbols`). Take top N where N = `charter.hard_caps.max_concurrent_positions - currently_open`.
- For each instrument NOT currently held:
  - `mcp__mt5-mcp__get_rates` for primary timeframes (the price-action skill knows the stack).
  - Pipe into `trading-agent-skills-price-action`.
  - If no candidate returned → no log (idle scan).
  - If candidate AND `charter.allowed_setups` is non-empty AND `candidate.setup_type` NOT in list → write skip with reasoning="setup_not_allowed".
  - Else (candidate, allowed):
    - Run `trading-agent-skills-checklist`.
    - If checklist == BLOCK → skip with reasoning="checklist_block: <reasons>".
    - If checklist == WARN → agent decides. May proceed at half size; MUST log decision (open or skip) with reasoning.
    - If checklist == PASS:
      - Run `trading-agent-skills-size` with `risk_pct = charter.hard_caps.per_trade_risk_pct` (or half if guardian==CAUTION).
      - **Write intent record FIRST** via `trading-agent-skills-journal decision write`.
      - Call `mcp__mt5-mcp__place_order` with the sized lot.
      - Write outcome record via `trading-agent-skills-journal decision write-outcome`.

### 6. End tick

Print a brief tick summary to the harness output:

```
tick 2026-04-30T22:00:00+00:00 done — 1 open / 0 close / 0 modify / 2 skip
```

Idle until the next harness trigger.

## Hard rules (non-negotiable)

- **NEVER call `place_order` / `close_position` / `modify_order` without first writing a decision-intent record** with `execution.execution_status: pending`.
- **NEVER exceed `charter.hard_caps.per_trade_risk_pct`** when invoking position-sizer.
- **NEVER open a position that would push concurrent count above `charter.hard_caps.max_concurrent_positions`**.
- **NEVER operate on live broker if `charter.mode != live`**. The mode flip is user-initiated only — see AGENTS.md "Demo→live runbook."
- **Honor guardian HALT immediately**. CAUTION halves the per-trade risk for the rest of the session.

## Safety rails

If anything goes wrong (any unexpected error from any subprocess or MCP tool):
1. Write a skip decision with `reasoning="tick_error: <repr>"`.
2. Exit the tick.
3. Do NOT retry within the tick. Next harness trigger re-evaluates cleanly.

## Out of scope for this skill

- Modifying the charter (only strategy-review can propose; only user can apply).
- Running weekly review (separate skill: strategy-review).
- Contacting the user (this skill is fully unattended).
- Mode flip demo↔live (separate user-initiated flow per AGENTS.md).

## Smoke test

After install, fire one manual tick to verify wiring. Replace `<id>` with your charter account_id.

```
TRADING_AGENT_ACCOUNT_ID=<id>
```

Then trigger the skill once. Expected: a tick summary line in the output, AND at least one record (likely a `weekend` or `all_markets_closed` skip if outside session) appearing in `~/.trading-agent-skills/accounts/<id>/decisions.jsonl`.
