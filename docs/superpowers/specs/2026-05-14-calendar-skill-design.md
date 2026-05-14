# Calendar Skill — Design Spec

**Date:** 2026-05-14
**Status:** Approved design, pending implementation plan

## 1. Goal

Expose Calix economic + earnings calendar lookups as a first-class skill that any agent (Claude Code main, MM cron stages, freeform Slack chat) can discover and invoke directly. Today, Calix is wrapped by `src/trading_agent_skills/calix_client.py` but only consumed indirectly inside `pre-trade-checklist` (proximity check at trade time) and `session-news-brief` (heavy fan-out with news APIs and AlphaVantage). There is no lean "show me what's on the calendar" or "what was the actual print" surface — and that gap let an agent (the OpenClaw `main` agent on `deepseek-v4-flash`) hallucinate "CPI tonight" two days after CPI had already printed, with no calendar source to ground the claim.

The new skill solves the discovery problem with a single CLI (`trading-agent-skills-calendar`) and a SKILL.md whose `description:` triggers any time the agent is about to reference a past or future economic event or earnings release.

## 2. Architecture

A thin CLI wrapper on top of the existing `CalixClient`. No Python work outside what's needed to expose past + find lookups; no refactor of `checklist.py` / `news_brief.py`. Pure-function enrichment computes the time fields the LLM actually needs (`minutes_until` / `minutes_since` / `local_time_aest`) so models don't have to do timezone math (which is exactly where the original hallucination happened).

```
agent (LLM)
   │  invokes skill, runs CLI
   ▼
trading-agent-skills-calendar economic past --currencies USD --impact High --limit 10
   │
   ▼
cli/calendar.py  (argparse, three-level: <noun> <verb> [flags])
   │  calls
   ▼
CalixClient.fetch_economic_past(...)  ← new method, mirrors fetch_economic
   │  HTTP GET (60s on-disk cache, 5s timeout, MockTransport injectable for tests)
   ▼
https://calix.fintrixmarkets.com/v1/calendar/economic/past
   │  returns {updatedAt, source, stale, events[]}
   ▼
calendar.enrich_events(payload, now_utc)  ← new pure function
   │  adds minutes_since/local_time_aest per event;
   │  filters by --within-hours / --within-days when supplied
   ▼
stdout: enriched JSON
```

Existing `pre-trade-checklist` and `session-news-brief` skills continue to fetch Calix via their current curl-then-pipe pattern. Refactoring them to use the new CLI is out of scope (could happen later, but neither is broken today).

## 3. Files

| Path | Change | Purpose |
|---|---|---|
| `src/trading_agent_skills/calix_client.py` | EDIT | Add `fetch_economic_past()` and `fetch_earnings_past()`. Same `_get` chokepoint and cache as the existing `fetch_economic` / `fetch_earnings`. |
| `src/trading_agent_skills/calendar.py` | NEW | Pure functions: `enrich_events(payload, now_utc)` and `find_events(events, *, title, currency, date)`. No I/O, no Decimal (times only). |
| `src/trading_agent_skills/cli/calendar.py` | NEW | Argparse front-end with three levels: `economic upcoming\|past\|find`, `earnings upcoming\|past`. |
| `tests/test_calix_client.py` | EDIT | Add ~4 tests for the new past methods (query-string assembly, cache reuse, error handling). |
| `tests/test_calendar.py` | NEW | Pure-function tests for `enrich_events` and `find_events`. ~10 cases. |
| `tests/test_cli_calendar.py` | NEW | End-to-end CLI tests using `httpx.MockTransport`. ~12 cases covering all subcommands + exit codes. |
| `pyproject.toml` | EDIT | Add `trading-agent-skills-calendar = "trading_agent_skills.cli.calendar:main"` entry point. |
| `.claude/skills/calendar/SKILL.md` | NEW | The skill description, when-to-invoke triggers, hard rules, and workflow body. |
| `CHANGELOG.md` | EDIT | "Unreleased" entry: new CLI + skill. |

Files **not** touched: `checklist.py`, `news_brief.py`, the existing `fetch_economic` / `fetch_earnings` methods (no breaking changes).

## 4. CLI surface

```
trading-agent-skills-calendar economic upcoming  [--currencies CSV] [--impact CSV] [--limit N] [--within-hours H] [--raw]
trading-agent-skills-calendar economic past      [--currencies CSV] [--impact CSV] [--limit N] [--within-hours H] [--raw]
trading-agent-skills-calendar economic find      --title SUBSTR [--currency CODE] [--date YYYY-MM-DD] [--days-back N=7] [--raw]

trading-agent-skills-calendar earnings upcoming  [--symbols CSV] [--limit N] [--within-days D] [--raw]
trading-agent-skills-calendar earnings past      [--symbols CSV] [--limit N] [--within-days D] [--raw]
```

Flag semantics:

- `--currencies` — comma-separated 3-letter codes (e.g. `USD,EUR`), or `majors` (default — USD/EUR/GBP/JPY/AUD/CAD/CHF/NZD), or `all`. Maps directly to Calix's `currencies=` query param.
- `--impact` — comma-separated `High,Medium,Low,Holiday`. Default `High`.
- `--limit` — int 1–25. Default 10. Calix caps at 25.
- `--symbols` — comma-separated tickers for earnings (e.g. `AAPL,MSFT`).
- `--within-hours` / `--within-days` — local post-fetch filter that drops events farther out than the bound. Defends against the agent over-extrapolating from a list (the "CPI 28 days away" problem becomes "no qualifying events" instead).
- `--raw` — escape hatch: skip enrichment, emit Calix payload bytes-equal to upstream.

`economic find` semantics (the convenience subcommand for "what did X print at"):

- Internally calls `/v1/calendar/economic/past` with broad filters (`currencies=all,impact=High,Medium,Low,Holiday`), then filters in-memory.
- Match rules: case-insensitive substring on `title`, optional exact match on `currency`, optional exact-date match on the date portion of `scheduledAt`.
- `--days-back` bounds how far back the in-memory filter looks. Default 7. Calix's past endpoint is already bounded by what's in its weekly cache, so this flag controls *output* filtering (drop matches older than N days), not request scoping.
- Returns:
  ```json
  {
    "query": {"title": "CPI", "currency": "USD", "date": "2026-05-12"},
    "matches": [<enriched event>, ...],
    "near_misses": [<events with same title+currency but different date>, ...],
    "fetched_at_utc": "2026-05-14T02:05:00Z",
    "source": "tradays",
    "stale": false
  }
  ```
- Exit 0 even when `matches: []`, so the agent can render "no match" cleanly. SKILL.md instructs the agent NOT to invent values to fill the gap.

Output enrichment (added to every event when `--raw` is not passed):

- For upcoming: `minutes_until` (int), `is_past` (bool, defensive), `local_time_aest` (string).
- For past economic: `minutes_since` (positive int), `local_time_aest`, `actual_present` (bool — explicit so agent doesn't have to handle `null` ambiguity).
- For past earnings: `days_since` (int), `actual_present` (bool, true iff `epsActual` or `revenueActual` is non-null).

Exit codes:

- `0` — success, including empty results.
- `2` — Calix unreachable, 5xx, or non-JSON response. Stdout is `{"error": "...", "source": "calix"}`.
- `64` — bad CLI args (argparse default).

## 5. SKILL.md trigger and rules

Frontmatter:

```yaml
---
name: calendar
description: Use BEFORE mentioning ANY economic event or earnings release — past or future. Covers (a) upcoming catalysts ("when is the next CPI", "any catalysts tonight"), (b) past prints ("what did CPI come in at", "did NFP beat", "PPI actual"), and (c) specific event lookups ("CPI on May 12"). Read-only — never invent event timing or values from training data; if this skill returns nothing, say nothing.
---
```

Hard rules in the body:

1. **Future events** — never reference an upcoming event without invoking `economic upcoming` or `earnings upcoming` in the same turn.
2. **Past values** — never quote a `forecast`, `previous`, or `actual` value without invoking `economic past` / `earnings past` / `economic find` and reading the value from JSON output. Confabulating "CPI printed 3.8%" is the same failure mode as confabulating "CPI tonight" — both are bullshit-from-training-data.
3. **No partial citation** — if the skill returns an event with `actual: null`, the answer is "the print hasn't been released yet" or "the data source doesn't have the actual value", NOT a guess based on consensus or news.
4. **Disambiguate when find returns multiple matches** — if `find` returns 3 different "CPI" events for the same date, present all three with their titles. Never pick one and pretend it's the canonical answer.
5. **Trust the staleness flag** — if `degraded: true`, caveat the response with "calendar feed flagged stale (>3h since refresh)".

Trigger phrases the description should catch (description does the work; these are listed in the body for reinforcement):

- Upcoming: "is there a CPI tonight", "when's the next NFP", "what's coming up this week", "any catalysts today"
- Past: "what did CPI print at", "did NFP beat", "PPI actual", "MSFT earnings beat?"
- Lookup: "CPI on May 12", "NFP last Friday", "AAPL Q1 EPS"
- Indirect: "I'm thinking about going long NAS100 — anything coming up?" → invoke before answering.

## 6. Testing strategy

Reuses the repo's existing patterns: no `unittest.mock`, real fixture factories, `httpx.MockTransport` for HTTP boundary, time injected as a parameter to keep tests deterministic.

`tests/test_calendar.py` (pure-function tests for `enrich_events` and `find_events`):

| Case | Input | Expectation |
|---|---|---|
| Future event enrichment | `scheduledAt = now + 2h` | `minutes_until = 120`, `is_past = False`, `local_time_aest` set |
| Past event enrichment | `scheduledAt = now - 5m` | `minutes_since = 5`, `is_past = True` |
| Boundary at `now` | `scheduledAt = now` | `minutes_until = 0`, `is_past = False` |
| `--within-hours 24` filter | event at 25h from now | Dropped from output |
| Earnings date-only | `scheduledDate = "2026-05-20"` | `days_until = 6`, no `minutes_until` |
| `actual_present` flag | event with `actual = "3.8%"` | `actual_present = True` |
| `actual_present` flag | event with `actual = null` | `actual_present = False` |
| `find` exact match | title="CPI", currency="USD", date matches | Single event in `matches` |
| `find` substring match | title="CPI" matches "CPI m/m" + "Core CPI m/m" + "CPI y/y" | All three in `matches` |
| `find` no match | title="UNICORN" | `matches: []`, may have `near_misses` |
| `find` near-miss | title="CPI", currency="USD", date wrong | `matches: []`, near_misses contains same-title+currency events |

`tests/test_cli_calendar.py` (end-to-end with `httpx.MockTransport`):

| Case | Mock response | Expectation |
|---|---|---|
| `economic upcoming` happy path | 200 + 3 future events | exit 0, JSON with enriched events |
| `economic past` happy path | 200 + 5 past events | exit 0, all have `minutes_since`, `actual_present` |
| `economic find --title CPI --currency USD --date 2026-05-12` | 200 + week of events | matches contains only CPI/Core CPI/CPI y/y on that date |
| `earnings upcoming` | 200 + 2 entries | exit 0, days_until set |
| `earnings past` | 200 + entry with `epsActual` | `actual_present = True` |
| `earnings past --symbols AAPL,MSFT` | 200 | request URL contains `symbols=AAPL,MSFT` |
| Stale upstream | 200 + `stale: true` | `degraded: true` in output, exit 0 |
| Empty result | 200 + `events: []` | exit 0, `events: []`, no error |
| 5xx response | 503 | exit 2, stdout `{"error": "...", "source": "calix"}` |
| Network error | `httpx.ConnectError` | exit 2, error JSON |
| `--raw` flag | 200 | output bytes-equal to upstream payload (no enrichment) |
| Bad CLI args | `economic` with no subcommand | exit 64 |

Total ~22 new test cases; should run in <150ms (no network, no sleeps). Existing `test_calix_client.py` already covers the underlying client mechanics.

## 7. Failure-mode contract

The skill defines a clear contract for the three failure modes that matter:

| Calix returns | CLI exit | Stdout shape | Agent's instructed response |
|---|---|---|---|
| 200 with results | 0 | enriched JSON | Quote `title`, `actual`/`forecast`, `local_time_aest` directly. |
| 200 with `events: []` | 0 | `{events: [], note?: ...}` | "No qualifying events in that window." Do NOT invent. |
| 200 with `stale: true` | 0 | `{..., degraded: true}` | Caveat: "calendar feed flagged stale (>3h)". |
| 4xx/5xx | 2 | `{error: "...", source: "calix"}` | "Calendar source unreachable — can't verify; not making any claims about events right now." |
| Network failure | 2 | `{error: "...", source: "calix"}` | Same as above. |

This contract is the entire point of the skill: it gives the LLM a deterministic ground-truth path so it never falls back on training-data guesses about event timing or values. The deepseek-v4-flash hallucination on 2026-05-14 ("CPI tonight 10:30 PM AEST") is the specific failure mode this skill prevents.

## 8. Out of scope (downstream wiring)

These are downstream consumers of the new skill — not part of this skill build but listed so they don't get lost:

| # | Where | What |
|---|---|---|
| 1 | vendor gitlink + pipx upgrade | Standard release dance after merging. Mirrors v0.3.0 flow on 2026-05-13: bump version, CHANGELOG, tag, push, bump workspace `vendor/agent-trading-skills` gitlink, `pipx upgrade trading-agent-skills`. |
| 2 | `~/.openclaw/workspace/prompts/morning-brief.md` | Insert a Step 1.5: `calendar economic upcoming --within-hours 24` and `calendar earnings upcoming --within-days 7`. Use results to populate "Today's setup" / "Watch" lines instead of relying on hand-typed daily memory. |
| 3 | `~/.openclaw/workspace/SOUL.md` (or main agent's standing context) | Add: "Never mention an upcoming or past economic event without invoking the `calendar` skill in the same turn. Confabulating event timing or values is the same as lying to the user." |
| 4 | `~/.openclaw/workspace/prompts/stage1.md` (news-window check) | Currently checks if we're *near* a news event; could also check `economic past --within-hours 1` and post-CPI react with the actual value vs forecast. Lets MM say "CPI printed 0.6% vs 0.7% forecast" instead of going silent or hallucinating. |
| 5 | `~/.openclaw/workspace/prompts/morning-brief.md` (recap) | Replace hand-typed "yesterday's CPI was hot" with `economic past --within-hours 24` so daily-memory recaps cite verified actuals. |
| 6 | Investigate routing main agent off `deepseek-v4-flash` for trade chat | Optional: deepseek-v4-flash hallucinated despite no stale data. Consider routing #mm freeform chat to mm-opus or mm-codex. |

Items 1–5 are straightforward edits in a follow-up session after this skill ships. Item 6 is a separate investigation tracked in operational notes.

## 9. What this design explicitly does NOT do

- Does not refactor `checklist.py` or `news_brief.py` to use the new CLI. They keep their existing curl-then-pipe pattern. (Could be a future cleanup but is unrelated to fixing the hallucination bug.)
- Does not add a watch-mode or polling daemon. The skill is a one-shot lookup; cron handles scheduling.
- Does not cache results across sessions beyond the existing 60s on-disk cache in `CalixClient`. Calix itself caches at the edge for 5min.
- Does not add Slack-formatted output. Output is structured JSON only; agents that need to post to Slack do their own formatting (matches the rest of the repo's CLI conventions).
- Does not add an `economic find` equivalent for earnings. The `--symbols` filter on `earnings past` already handles "did AAPL beat last quarter" cleanly; a separate find subcommand would be redundant.
