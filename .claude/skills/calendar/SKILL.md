---
name: calendar
description: Use BEFORE mentioning ANY economic event or earnings release — past or future. Covers (a) upcoming catalysts ("when is the next CPI", "any catalysts tonight"), (b) past prints ("what did CPI come in at", "did NFP beat", "PPI actual"), and (c) specific event lookups ("CPI on May 12"). Read-only — never invent event timing or values from training data; if this skill returns nothing, say nothing.
---

# Calendar

Looks up upcoming and past economic calendar events (CPI, NFP, FOMC, GDP, PMI, central-bank decisions) and corporate earnings releases via the Calix API. Returns structured JSON with computed time fields (`minutes_until` / `minutes_since` / `local_time_aest`) so you don't have to do timezone math.

This skill is the single source of truth for "what's on the calendar" and "what did X print at." Never answer either question from training data.

## Hard rules

1. **Future events** — never reference an upcoming event without invoking `economic upcoming` or `earnings upcoming` in the same turn. The data has a release schedule that changes weekly; your training data does not.
2. **Past values** — never quote a `forecast`, `previous`, or `actual` value without invoking `economic past` / `earnings past` / `economic find` and reading the value directly from JSON output. Confabulating "CPI printed 3.8%" is the same failure mode as confabulating "CPI tonight" — both are bullshit-from-training-data.
3. **No partial citation** — if the skill returns an event with `actual: null`, the answer is "the print hasn't been released yet" or "the data source doesn't have the actual value." NOT a guess based on consensus or news.
4. **Disambiguate when find returns multiple matches** — if `find` returns 3 different "CPI" events for the same date (CPI m/m, CPI y/y, Core CPI m/m), present all three with their titles. Never pick one and pretend it's the canonical answer.
5. **Trust the staleness flag** — if the response includes `degraded: true`, caveat your answer with "calendar feed flagged stale (>3h since refresh)".

## Prerequisites

The CLI is `trading-agent-skills-calendar`. Verify with:

```bash
trading-agent-skills-calendar --help
```

If not found: install the Python package — from the agent-trading-skills repo, run `pip install -e .` in a venv your harness can see.

The Calix endpoint is `https://calix.fintrixmarkets.com`. No auth required; results are edge-cached for 5 minutes.

## When to invoke

**Upcoming:**
- "is there a CPI tonight" / "when's the next NFP"
- "what's coming up this week" / "any catalysts today"
- "should I be careful around news today"

**Past:**
- "what did CPI come in at" / "did NFP beat"
- "PPI actual" / "MSFT earnings beat?"
- "what was the print on the May 12 CPI"

**Specific lookup (use `find`):**
- "CPI on May 12" / "NFP last Friday" / "AAPL Q1 EPS"

**Indirect:** when a user is discussing a trade and you're about to mention upcoming or past macro events as part of the analysis, invoke first. E.g.:

> User: "I'm thinking about going long NAS100 — anything coming up?"

→ Call `economic upcoming --currencies USD --within-hours 48` BEFORE answering.

## CLI surface

```
trading-agent-skills-calendar economic upcoming  [--currencies USD,EUR | majors | all] [--impact High,Medium,Low,Holiday] [--limit N] [--within-hours H] [--raw]
trading-agent-skills-calendar economic past      [--currencies …] [--impact …] [--limit N] [--within-hours H] [--raw]
trading-agent-skills-calendar economic find      --title SUBSTR [--currency CODE] [--date YYYY-MM-DD] [--days-back N=7] [--raw]

trading-agent-skills-calendar earnings upcoming  [--symbols AAPL,MSFT] [--limit N] [--within-days D] [--raw]
trading-agent-skills-calendar earnings past      [--symbols …] [--limit N] [--within-days D] [--raw]
```

Defaults: `--currencies majors` (USD/EUR/GBP/JPY/AUD/CAD/CHF/NZD), `--impact High`, `--limit 10`. Calix caps `--limit` at 25.

## Output shape

**Economic upcoming/past:**
```json
{
  "updatedAt": "2026-05-14T02:01:33Z",
  "source": "tradays",
  "stale": false,
  "degraded": false,
  "fetched_at_utc": "2026-05-14T02:05:00+00:00",
  "now_utc": "2026-05-14T02:05:00+00:00",
  "events": [
    {
      "id": "ff_usd_cpi_y_y_20260512",
      "title": "CPI y/y",
      "currency": "USD",
      "impact": "High",
      "scheduledAt": "2026-05-12T12:30:00.000Z",
      "forecast": "3.7%",
      "previous": "3.3%",
      "actual": "3.8%",
      "minutes_until": -2375,
      "minutes_since": 2375,
      "is_past": true,
      "local_time_aest": "2026-05-12 22:30 AEST",
      "actual_present": true
    }
  ]
}
```

**Economic find:**
```json
{
  "query": {"title": "CPI", "currency": "USD", "date": "2026-05-12"},
  "matches": [],
  "near_misses": [],
  "fetched_at_utc": "...",
  "source": "tradays",
  "stale": false
}
```

**Earnings:** same shape, with `earnings[]` instead of `events[]`, `days_until`/`days_since` instead of `minutes_*`, and `epsEstimate`/`epsActual`/`revenueEstimate`/`revenueActual` fields populated on past entries.

## Handling responses

| Output | What it means | What you say |
|---|---|---|
| `events: [<...>]` (or `earnings`) | Hits found. | Quote `title`, `actual`/`forecast`, `local_time_aest` directly from the JSON. |
| `events: []` | No qualifying events. | "No qualifying events in that window." Do NOT invent. |
| `degraded: true` | Calix's cache is >3h stale. | Caveat: "calendar feed flagged stale". |
| Exit code 2 + `{error, source: "calix"}` | Calix unreachable / 5xx / network error. | "Calendar source unreachable — can't verify; not making any claims about events right now." |

## Workflow

1. **Pick the right subcommand** based on the question (upcoming / past / find).
2. **Tighten the filter** — if the user asked about a specific currency or symbol, use `--currencies` / `--symbols`. If the question is time-bounded, use `--within-hours` / `--within-days`. Tight filters mean fewer hallucination opportunities.
3. **Run the CLI** and read the JSON output.
4. **Apply the rules above** when shaping your answer.
5. **For `find` with multiple matches** — list every match. Do not collapse to one number.
