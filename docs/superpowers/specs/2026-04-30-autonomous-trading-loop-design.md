# autonomous-trading-loop — design

**Status:** draft, awaiting user review
**Date:** 2026-04-30
**Author:** Claude (brainstorming session with Vincent)
**Sibling skills already shipped:** position-sizer, trade-journal, daily-risk-guardian, pre-trade-checklist, session-news-brief, price-action

---

## 1. Purpose

Enable an agent harness (Claude Code, OpenClaw, Hermes) to operate a connected MT5 demo account autonomously on a recurring heartbeat — opening, modifying, and closing positions using the existing six advisory skills — while preserving full reasoning audit trail and keeping a human user as the only gate for changes to the operating envelope.

The mental model: **"Vincent, I've set up a demo account. Go trade it. Tell me what you did and why. Every Sunday we'll review and tweak the rules."**

## 2. Non-goals (v1)

- **No live-account autonomy by default.** `mode: demo` is the install default. Live requires user-initiated, charter-resident flag flip.
- **No automated mode→live transition.** The strategy-review skill cannot propose `mode` changes.
- **No backtesting.** mt5-mcp doesn't currently expose MT5's strategy tester; tracked in `FUTURE.md` as a follow-up that requires upstream work in mt5-mcp.
- **No 24/7 long-running process.** The architecture is heartbeat-based — each tick is stateless except for what's in journal/decision-log/charter. The harness fires the tick on its own schedule.
- **No multi-account simultaneous trading.** A given charter binds to one `account_id`. Switching accounts is a fresh-start operation by design.
- **No new core analysis logic.** The four new pieces are an extension to `trade-journal`, two new skills (`trading-heartbeat`, `strategy-review`), and a charter file. None of them reimplement structure detection, news fetching, or risk computation — they orchestrate the existing skills.

## 3. Deliverables (4 pieces)

| # | Deliverable | Type | Size |
|---|---|---|---|
| 1 | `trade-journal decision` subcommand | Extension to existing skill | ~150 LOC + tests |
| 2 | `charter.md` template + install Q&A | Doc + AGENTS.md update | ~100 lines |
| 3 | `trading-heartbeat` skill | New skill (orchestrator, no Python) | ~80-line SKILL.md |
| 4 | `strategy-review` skill | New skill (analytics + proposal) | ~300 LOC + SKILL.md |

Plus: state-layout migration to per-account namespacing under `~/.trading-agent-skills/accounts/<account_id>/`.

## 4. State layout

### 4.1 Per-account namespace

```
~/.trading-agent-skills/
  config.toml                            # global defaults (existing, unchanged)
  spread_baseline.json                   # global, account-agnostic (existing)
  calix_cache/                           # global (existing)
  news_cache/                            # global (existing)
  .env                                   # global API keys (existing)
  accounts/                              # NEW
    <account_id>/                        #   one dir per MT5 account (e.g. 12345678/)
      charter.md                         #   operating envelope
      charter_versions/                  #   audit trail of past charter versions
        v1.md
        v2.md
        ...
      journal.jsonl                      #   completed trades (existing format, moved here)
      decisions.jsonl                    #   NEW: intent records, append-only
      proposals/                         #   weekly strategy-review outputs
        2026-05-04.md
        ...
      daily_state.json                   #   NY-close session bookkeeping (existing format, moved here)
```

### 4.2 Backward compatibility

`~/.trading-agent-skills/journal.jsonl` and `~/.trading-agent-skills/daily_state.json` already exist for non-autonomous use. To avoid breaking existing pull-based skill invocations:

- **Reads** check `accounts/<id>/journal.jsonl` first (when an account context is active), fall back to root-level files.
- **Writes** by autonomous-mode skills go to per-account paths.
- **Writes** by manual `trade-journal write` (no account context) go to root-level for backward compat. We document that users adopting autonomous mode should migrate manually (one-time `mv`).

Account context is established by reading `charter.md` from the most recently modified `accounts/<id>/` directory at session start. If only one account dir exists, no ambiguity. If multiple exist, the harness is expected to pass `TRADING_AGENT_ACCOUNT_ID` env var or fail loudly.

**Known wart (acceptable for v1):** if a user runs `trading-agent-skills-journal write` manually (no account context) **while** autonomous mode is also operating that account, the manual write goes to the root-level file and is invisible to the autonomous tools. Documented in AGENTS.md; recommended pattern is "always pass `--account-id` once autonomous mode is set up." A future v2 could auto-detect active charter and route writes accordingly.

## 5. Charter

### 5.1 Hard fields (required, enforced)

```yaml
mode: demo                              # demo | live
account_id: 12345678                    # MT5 account number, must match get_account_info
heartbeat: 1h                           # 15m | 1h | 4h
hard_caps:
  per_trade_risk_pct: 1.0
  daily_loss_pct: 5.0                   # forwarded to daily-risk-guardian
  max_concurrent_positions: 3
charter_version: 1                      # increments on each accepted strategy-review proposal
created_at: 2026-04-30T14:00:00+10:00   # ISO 8601 with offset
created_account_balance: 10000.00       # snapshot at install for context
```

**Validation rules:**

- `mode` MUST be `demo` or `live` (no other values).
- `account_id` MUST match the broker's `get_account_info().login` at every heartbeat tick. Mismatch → tick aborts with `account_mismatch` decision record, no execution.
- `heartbeat` MUST match the trading style implied by user's notes (style→cadence map below). Mismatched → install Q&A re-prompts; mid-life mismatches are surfaced in strategy-review.
- `hard_caps.per_trade_risk_pct` upper-bounded at 5.0 (sanity).
- `hard_caps.daily_loss_pct` upper-bounded at 20.0 (sanity).
- `hard_caps.max_concurrent_positions` upper-bounded at 20.

Style ↔ heartbeat defaults (suggested, not hard-locked):

| Style | Heartbeat default | Acceptable range |
|---|---|---|
| scalp | 15m | 5m–15m |
| day | 1h | 30m–1h |
| swing | 4h | 1h–4h |

### 5.2 Soft fields (optional, advisory)

```yaml
trading_style: day                      # scalp | day | swing — informs heartbeat default
sessions_allowed: []                    # subset of [tokyo, london, ny]; empty = any
instruments: []                         # subset of broker symbols; empty = use 5-tier resolver
allowed_setups: []                      # free-form strategy labels; empty = any
                                        # examples: "price_action:pin_bar",
                                        #   "news_brief:swing_candidate",
                                        #   "swap_harvest", "manual_target"
notes: ""                               # free-form trading philosophy, agent-readable
```

**Behavior when soft field is empty:**

- `instruments: []` → tick uses `session-news-brief` 5-tier watchlist resolver (positions / Calix calendar / vol-leaders / explicit / default).
- `allowed_setups: []` → no filter; agent labels each entry's `setup_type` in the decision log freely.
- `sessions_allowed: []` → tick may act in any session that's open.
- `notes: ""` → agent has no philosophical bias; relies purely on technical/risk skills.

**Critical:** `allowed_setups` is **not** restricted to price-action's 9 detectors. It's a free-form label space. The agent self-labels its entries; the strategy-review skill aggregates what's been working under each label and can propose adding/removing labels from `allowed_setups`.

### 5.3 Locked fields (cannot be proposed by strategy-review)

| Field | Why locked |
|---|---|
| `mode` | Only user-initiated demo→live flip is allowed |
| `account_id` | Account change = fresh start, not in-place modification |
| `created_at` | Audit |
| `created_account_balance` | Audit |
| `charter_version` | System-managed |

All other fields (hard caps, soft fields) are proposable, with user approval required before write.

## 6. Decision log

### 6.1 Schema

Stored at `accounts/<account_id>/decisions.jsonl`, append-only. One record per executed action OR per evaluated-but-skipped candidate.

```jsonc
{
  "ts": "2026-04-30T22:14:00Z",            // UTC, ISO 8601
  "kind": "open",                          // "open" | "modify" | "close" | "skip"
  "symbol": "XAUUSD.z",
  "ticket": 12345,                         // null for skip-of-no-position; broker ticket for open/modify/close/skip-on-existing-position
  "setup_type": "price_action:pin_bar",    // free-form label, must match charter.allowed_setups if non-empty
                                           // REQUIRED for kind=open and kind=skip
                                           // OPTIONAL for kind=close/modify (may copy from the linked open record)
  "reasoning": "Pullback to 2380 support, strong pin bar rejection on H1. Long, SL 2375, TP 2395.",
  "skills_used": ["price-action", "pre-trade-checklist", "position-sizer"],
  "guardian_status": "CLEAR",              // CLEAR | CAUTION | HALT — copied from guardian invocation
  "checklist_verdict": "PASS",             // PASS | WARN | BLOCK | null (for close/modify)
  "execution": {                           // only for kind in {open, modify, close}
    "side": "BUY",                         // BUY | SELL | null (for skip/modify-no-side)
    "volume": "0.08",                      // string, Decimal-as-string convention
    "entry_price": "2380.50",
    "sl": "2375.00",
    "tp": "2395.00",
    "execution_status": "filled"           // "pending" | "filled" | "rejected" | "broker_error"
  },
  "charter_version": 3,                    // version of charter at decision time
  "tick_id": "2026-04-30T22:00:00Z"        // ISO ts of the heartbeat tick that produced this
}
```

### 6.2 Write-before-execute rule

The trading-heartbeat SKILL.md instructs the agent to:

1. Compose the decision record with `execution.execution_status: "pending"` and a freshly generated `tick_id` matching the heartbeat tick timestamp.
2. Append to `decisions.jsonl` (this is the **intent record**).
3. Call mt5-mcp execution tool (`place_order` / `close_position` / `modify_order`).
4. On success: append a follow-up **outcome record** with the same `(tick_id, kind, symbol, ticket-after-broker-assigns)` and `execution_status: "filled"`. Do NOT mutate the prior intent record — append-only.
5. On failure: append a follow-up outcome record with `execution_status: "rejected"` or `"broker_error"` and a `failure_reason` field.

**Reader-side reconciliation:** the decision-log reader joins records by `(tick_id, kind, symbol)` and treats the **latest** record by `ts` as authoritative for execution state. The intent record's `reasoning` is preserved as the canonical "why"; the outcome record's role is to update execution_status and (for opens) record the broker-assigned ticket and actual fill price. This guarantees the reasoning is captured before the broker call, so a crash mid-flight still leaves a trail.

For opens: the intent record's `ticket` field is `null` (broker hasn't assigned one yet); the outcome record carries the assigned ticket. For modify/close: both records share the original ticket.

### 6.3 What counts as a `skip`

- `skip` is logged ONLY when a candidate was actually evaluated (price-action returned ≥1 candidate, OR pre-trade-checklist was invoked, OR the agent was about to act and a hard rule blocked it).
- "Idle scan, nothing of interest" is NOT logged. This prevents 95%-noise decision logs.
- Logged skips MUST include `reasoning` explaining what was rejected and why.

### 6.4 Subcommand surface

```bash
# Append a decision (called by trading-heartbeat tick)
echo '<json>' | trading-agent-skills-journal decision write --account-id 12345678

# Read decisions for analysis (used by strategy-review)
trading-agent-skills-journal decision read --account-id 12345678 --since 7d

# Filter by kind/symbol
trading-agent-skills-journal decision read --account-id 12345678 --since 7d --kind skip --symbol XAUUSD.z
```

Existing `trade-journal write` / `stats` subcommands continue to work unchanged. The `decision` subcommand is purely additive.

## 7. trading-heartbeat skill

A thin orchestrator skill, no Python. Lives at `.claude/skills/trading-heartbeat/SKILL.md`. Composes existing skills + mt5-mcp execution tools.

### 7.1 Trigger

The harness fires this skill on a cron/loop schedule matching `charter.heartbeat`:

| Harness | Trigger mechanism |
|---|---|
| Claude Code | `/loop 1h /trading-heartbeat` (or 15m / 4h) |
| OpenClaw | internal cron entry pointing to skill |
| Hermes | heartbeat system entry pointing to skill |

The same SKILL.md works across all three; harness-specific install instructions in AGENTS.md.

### 7.2 Tick cycle (deterministic)

```
1. Read charter.md for current account context.
   - If charter not found → log "no_charter" skip, exit tick.
   - If mode != demo AND mode != live → log "invalid_mode" skip, exit tick.

2. Verify broker connection.
   - get_account_info via mt5-mcp.
   - If account_info.login != charter.account_id → log "account_mismatch" skip, exit tick.
   - If broker unreachable → log "broker_unreachable" skip, exit tick.

3. Check kill conditions.
   - Run daily-risk-guardian. If status == HALT → log "guardian_halt" skip, exit tick.
   - If current local time falls outside charter.sessions_allowed (when set) → log "session_closed" skip, exit tick.
   - For each instrument in the resolved instrument list, query mt5-mcp `get_market_hours`. If ALL instruments report market closed → log one "all_markets_closed" skip per tick, exit. (Handles Friday-close → Sunday-open forex window correctly without UTC weekend assumptions.)

4. Manage open positions.
   - get_positions via mt5-mcp.
   - For each position:
     a. Run price-action on the position's primary TF.
     b. Run daily-risk-guardian classification (AT_RISK / RISK_FREE / LOCKED_PROFIT).
     c. If structural invalidation OR TP hit OR SL trail warranted:
        - Compose decision (kind=close or kind=modify).
        - Write decision (pending).
        - Execute via close_position / modify_order.
        - Write follow-up (filled/rejected).
     d. Else: do nothing, no log entry (boring "still holding" is noise).

5. Scan for new entries.
   - Resolve instrument list:
     - If charter.instruments non-empty → use it.
     - Else → invoke session-news-brief 5-tier resolver, take top N (N = charter.hard_caps.max_concurrent_positions - currently_open).
   - For each instrument not currently held:
     a. price-action scan.
     b. If candidate returned AND (charter.allowed_setups empty OR candidate.setup_type in charter.allowed_setups):
        - pre-trade-checklist.
        - If checklist == PASS:
          - position-sizer (using charter.hard_caps.per_trade_risk_pct).
          - Compose decision (kind=open, execution.execution_status=pending).
          - Write decision.
          - Execute via place_order.
          - Write follow-up (filled/rejected).
        - If checklist == WARN: agent decides — may proceed with reduced size, must log decision (open or skip) with reasoning.
        - If checklist == BLOCK: log skip with checklist verdict, do not execute.
     c. If price-action returns no candidate: no log entry (idle scan).

6. End tick. Idle until next harness trigger.
```

### 7.3 Hard rules enforced by the SKILL.md (not by Python)

The skill prompt explicitly tells the agent:

- "Never call `place_order`, `close_position`, or `modify_order` without first writing a decision record with `execution.execution_status: pending`."
- "Never exceed `charter.hard_caps.per_trade_risk_pct` when calling position-sizer."
- "Never open a position that would push concurrent count above `charter.hard_caps.max_concurrent_positions`."
- "Never operate when `mode != demo` unless user has explicitly flipped charter to `live` (and broker `account_info` confirms account_id matches)."
- "Honor daily-risk-guardian HALT immediately. CAUTION lowers per_trade_risk_pct by 50% for the rest of the session."

These are prompt-level instructions because the skill is markdown orchestration; the actual values are read from charter at tick time.

### 7.4 What the heartbeat skill does NOT do

- Does not modify the charter.
- Does not run weekly review.
- Does not propose strategy changes.
- Does not contact the user — fully unattended.
- Does not retry failed executions automatically (logs the failure; next tick will re-evaluate from current broker state).

## 8. strategy-review skill

A new skill at `.claude/skills/strategy-review/SKILL.md` + Python at `src/trading_agent_skills/strategy_review.py`. Fires weekly (or on-demand).

### 8.1 Trigger

| Harness | Trigger |
|---|---|
| Claude Code | User-invoked (`/strategy-review`) OR `/schedule weekly /strategy-review` |
| OpenClaw / Hermes | Cron entry on the user's preferred day/time |

Default cadence: weekly, Sunday evening user-local time. Configurable via charter (future field) or harness settings.

### 8.2 Inputs

- `accounts/<id>/charter.md` (current)
- `accounts/<id>/journal.jsonl` (last 7 days, plus all-time stats)
- `accounts/<id>/decisions.jsonl` (last 7 days)
- `~/.trading-agent-skills/spread_baseline.json` (per-symbol cost picture)
- `~/.trading-agent-skills/calix_cache/` (recent macro context)

### 8.3 Outputs

A markdown proposal at `accounts/<id>/proposals/YYYY-MM-DD.md` with:

```markdown
# Strategy review — 2026-05-04

## Performance summary (last 7 days)
- Trades closed: N (W wins / L losses, win rate X%)
- R-multiple expectancy: +0.42R
- P&L (deposit ccy): +$X
- Best setup label: "price_action:pin_bar" (3W/0L, +1.8R)
- Worst setup label: "swap_harvest" (0W/2L, -1.5R)

## Decision-log analysis
- 47 ticks, 12 evaluations, 4 entries, 8 skips
- Most common skip reason: "spread > 1.5x baseline" (5 occurrences)

## Charter diff proposal (requires approval)

```diff
 hard_caps:
-  per_trade_risk_pct: 1.0
+  per_trade_risk_pct: 0.8

 allowed_setups:
   - "price_action:pin_bar"
-  - "swap_harvest"
+  - "price_action:fvg_fill"
```

### Reasoning
- Tighten per-trade risk: drawdown peaked at 3.2% on Tuesday. Tighter cap reduces tail risk.
- Drop swap_harvest: 0/2 wins, holding cost not materializing as expected on UKOIL this week.
- Add fvg_fill: not currently allowed but observed 3 high-quality candidates in skips. Propose enabling to test.

## Reply with:
- "approve all" — apply every change above
- "approve <fields>" — apply only listed (e.g., "approve per_trade_risk_pct, allowed_setups")
- "reject" — no changes; proposal archived as-is
- "discuss <topic>" — ask clarifying question
```

### 8.4 User approval flow

1. User reads proposal.
2. Replies with `approve all` / `approve <field>...` / `reject` / `discuss`.
3. On approve: skill diffs current charter, writes new charter with `charter_version` incremented, archives prior charter to `charter_versions/v<N>.md`.
4. Proposal file is preserved as-is regardless of outcome (audit trail).

### 8.5 Locked-field protection

The `strategy_review.py` proposal generator MUST NOT emit changes to:

- `mode`
- `account_id`
- `created_at`
- `created_account_balance`
- `charter_version`

Tested via unit test that feeds engineered scenarios trying to provoke locked-field changes and asserts the proposal contains none.

### 8.6 What strategy-review does NOT do

- Does not auto-apply changes.
- Does not run trades.
- Does not contact external services beyond reading existing caches.
- Does not propose mode flips (demo→live).

## 9. Demo→live transition

User-initiated only. Flow:

1. User says: "I want to switch <account_id> to live."
2. Agent (in any active session, no special skill) reads:
   - Current `charter.md` (full text)
   - Last 4 weekly review summaries
   - All-time stats from `journal.jsonl`
   - Current account balance via `get_account_info`
3. Agent presents a single confirmation summary.
4. Agent asks: "Confirm switching <account_id> to live trading? Reply 'yes' to proceed, anything else to cancel."
5. On `yes`: agent updates `charter.md` setting `mode: live`, archives prior charter version, writes a `mode_changed` decision record with `kind: "mode_change"` (extension to decision schema for this one event type).
6. Subsequent heartbeat ticks operate against live account.

If user wants to revert: same flow, says "switch <account_id> back to demo". No additional ceremony — symmetric.

**Hard constraint:** strategy-review.py refuses to emit any proposal modifying `mode`. The `mode_change` decision record kind is reserved for human-initiated transitions and the heartbeat skill's prompt explicitly forbids generating one.

## 10. Install flow (charter Q&A)

Extends `AGENTS.md` with a new section: "Setting up autonomous trading."

### 10.1 Triggered by

User saying any of:
- "Set up autonomous trading"
- "I want the agent to trade my demo account"
- "Configure trading-heartbeat"
- "Initialize charter"

### 10.2 Q&A flow

Agent walks user through hard fields with sensible defaults, skips soft fields unless user volunteers them.

```
Agent: "What's the MT5 demo account number?"
User: 12345678

Agent: "Confirming via get_account_info... balance is $10,000.00 USD, server is FintrixMarkets-Demo. Correct account?"
User: yes

Agent: "What's your trading style? scalp / day / swing?"
User: day

Agent: "Day style. Heartbeat default is 1h. Override? (15m/30m/1h)"
User: 1h

Agent: "Per-trade risk cap (% of equity)? Default 1.0%"
User: 1.0

Agent: "Daily loss cap (% of equity)? Default 5.0%"
User: 5.0

Agent: "Max concurrent positions? Default 3"
User: 3

Agent: "That covers the hard rules. Want to constrain instruments, sessions, or setups,
        or leave it open and let me decide each tick?"
User: leave it open

Agent: "Done. Charter v1 written to ~/.trading-agent-skills/accounts/12345678/charter.md.
        Mode: demo. To start the heartbeat in Claude Code, run:
            /loop 1h /trading-heartbeat
        For OpenClaw/Hermes, see AGENTS.md §<n>."
```

If user volunteers soft constraints ("only NAS100 and gold for now"), agent fills the soft fields and confirms.

### 10.3 Offline install (broker not connected)

If mt5-mcp is unreachable during install Q&A:

- Skip the `get_account_info` confirmation step. Use the user-supplied `account_id` verbatim.
- Charter writes successfully. `created_account_balance` is omitted (not auto-filled with bogus value).
- First heartbeat tick will run the `account_id` mismatch / broker-unreachable checks (§7.2 step 2) and either succeed or log a `broker_unreachable`/`account_mismatch` skip until the user resolves connection.

This keeps install non-blocking — the user can configure when offline (e.g., on a flight) and the system self-validates on first tick.

### 10.4 Reading existing config

If `~/.trading-agent-skills/config.toml` already has `risk_per_trade_pct` etc., agent uses those as defaults in the Q&A. Avoids re-asking what's already known.

## 11. Cost model

Approximate per-tick LLM cost on Haiku 4.5 (recommended for heartbeat):

- Charter read + guardian + position scan + 0–N candidate evaluations
- Tokens per tick: ~5K input + 2K output average
- Cost per tick on Haiku: ~$0.03–0.10

Weekly totals by style:

| Style | Ticks/week | Heartbeat cost | + Strategy-review (Opus) | Total/week |
|---|---:|---:|---:|---:|
| swing (4h) | ~10 | $0.30–1.00 | $1–3 | $1–4 |
| day (1h) | ~40 | $1–4 | $1–3 | $2–7 |
| scalp (15m) | ~160 | $5–16 | $1–3 | $6–19 |

Documented in AGENTS.md as a heads-up. Not enforced — user picks model + style.

**Recommendation for harness configs:** Haiku for heartbeat, Opus for strategy-review (weekly cadence justifies the better model for pattern recognition + proposal quality).

## 12. Failure modes & handling

| Failure | Behavior |
|---|---|
| mt5-mcp unreachable | Log `broker_unreachable` skip, exit tick. No retry. |
| Account balance < charter.created_account_balance × 0.5 | Log warning in decision record but don't auto-stop (user awareness via weekly review) |
| All news APIs down | Tick proceeds with `news_starvation: true` flag in any decision record; user can configure kill-on-starvation in charter v2 if desired |
| Calix unreachable | Tick proceeds (calendar context degraded, not fatal) |
| place_order returns broker_error | Log `broker_error` follow-up, no retry within tick. Next tick re-evaluates from current state. |
| Charter file corrupt / unparseable | Log `charter_invalid` skip, exit tick. User must manually fix. |
| account_id mismatch (broker says different account) | Log `account_mismatch` skip, exit tick. Loud failure — should never happen in normal operation. |
| guardian == HALT | Log `guardian_halt` skip, exit tick. Resumes naturally next tick if guardian clears (usually next NY-close session reset). |
| All charter instruments closed (forex weekend, holiday) | Log one `all_markets_closed` skip per tick, exit. Detected via per-instrument `get_market_hours`. |

## 13. Testing strategy

### 13.1 Decision log extension

Standard fixture-driven pytest, parity with existing `journal_io.py` tests:

- Schema validation (rejects missing required fields, naive datetimes, unknown `kind`)
- Append semantics (concurrent write safety not required — single-writer assumption)
- `decision read` filters (`--since`, `--kind`, `--symbol`, `--account-id`)
- Locked-field protection in proposals (engineered scenarios)

### 13.2 strategy-review skill

- Synthetic journal + decision-log fixtures producing known patterns
- Assert proposals never touch `mode` / `account_id` / `created_at` / `created_account_balance` / `charter_version`
- Assert charter version increments on apply
- Assert prior charter archived to `charter_versions/v<N>.md`
- Assert proposal file persists regardless of approve/reject outcome

### 13.3 trading-heartbeat skill

Hard to unit-test (markdown skill, no Python). Coverage approach:

- The Python helpers it depends on (decision write, charter read) ARE unit-tested.
- The cycle correctness is verified end-to-end via live demo smoke test (round 4 / round 5 of the smoke-test sequence already in memory). User runs `/loop` with a fast cadence (15m) for one session, inspects decision log + journal + position state.

### 13.4 Charter parser

- Round-trip YAML preservation
- Validation rejection cases (mode, heartbeat, hard_caps bounds)
- Soft-field defaults (empty list ↔ "agent picks")

## 14. Open questions / future TODOs

| Item | Notes |
|---|---|
| MT5 strategy-tester / backtest support in mt5-mcp | Listed in `FUTURE.md`. Would unlock simulation before live commitment, validate strategy-review proposals against historical data. |
| Correlation matrix for exposure-overlap | Already a known plan-level TODO; orthogonal to autonomous trading but blocks high-quality risk capping for correlated positions in heartbeat tick. |
| Sentiment classification on news articles | Plan-level TODO; would feed into entry skip reasons. |
| Multi-account simultaneous trading | Out of scope v1. Would require concurrency model (which account ticks first when two heartbeats overlap) — not tackling. |
| Fractional charter rollback | If user accepts some fields and rejects others, current design re-emits a clean diff. No partial-undo of a previously-accepted proposal — user manually reverts via next strategy-review or hand-edit. |
| Auto-pause on extended drawdown | Charter could grow a `pause_after_consecutive_losses: N` field in v2. Not in v1 to avoid premature complexity. |

## 15. Migration / rollout

1. Land trade-journal `decision` subcommand + per-account-id namespacing (extension is non-breaking; root-level files remain readable).
2. Land charter parser + AGENTS.md install Q&A. Ship `trading-heartbeat` SKILL.md.
3. User runs install Q&A, gets charter v1.
4. User starts heartbeat via `/loop` (Claude Code) or harness cron (OpenClaw/Hermes).
5. After 7 days, user runs `/strategy-review` (or scheduled).
6. Approve/reject loop establishes the rhythm.
7. After ~4 weeks of demo, if performance looks credible, user manually flips `mode: live` via the user-initiated transition flow.

## 16. Conventions preserved

- Conventional commit messages, no `Co-Authored-By:` trailer.
- Decimal-typed money/price/volume; reject floats at boundaries.
- JSON-stdin → pure function → JSON-stdout for any new CLI surface.
- Hand-rolled fixture factories over mocks.
- Strict validation at write boundaries (decision log, charter).
- All new state files under `~/.trading-agent-skills/` with per-account namespacing.

## 17. What this design explicitly avoids

- Wizard skill (rejected during brainstorming — config Q&A in AGENTS.md instead).
- `trading-strategy.md` narrative file (rejected — markdown invites hallucinated interpretation).
- Verbatim-phrase demo→live gate (rejected as theatre — single confirmation with full context shown is sufficient).
- 24/7 long-running agent loops (rejected — heartbeat ticks are stateless).
- Backtest in v1 (deferred — requires mt5-mcp upstream work).
- Live mode on install (always demo on install; flip is explicit, user-initiated).
- Strategy-review proposing mode changes (forbidden — only user-initiated).
- Multi-account simultaneous operation (one account per charter; switch = fresh start).
