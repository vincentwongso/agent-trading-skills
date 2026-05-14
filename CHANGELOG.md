# Changelog

All notable changes to this project will be documented here. Format roughly follows [Keep a Changelog](https://keepachangelog.com/), versioning follows [SemVer](https://semver.org/).

## Unreleased

### Added
- `trading-agent-skills-calendar` CLI with `economic upcoming|past|find` and `earnings upcoming|past` subcommands. Wraps the existing `CalixClient`, adds time enrichment (`minutes_until`, `minutes_since`, `local_time_aest`), and exposes `find_events` for "what did X print at" lookups.
- `.claude/skills/calendar/SKILL.md` — first-class calendar skill so any agent can ground event-timing and event-value claims in Calix data instead of confabulating from training data.
- `CalixClient.fetch_economic_past()` and `CalixClient.fetch_earnings_past()`. Earnings methods now accept an optional `symbols` filter.

## 0.3.0 — 2026-05-13

Phase B of the Option β SQLite migration: `decisions.jsonl` now dual-writes to `trader.db`.

### Added
- `decisions_io` module: `append`, `read_raw`, plus `_normalize` / `_canonical_payload` / `_dedup_key` helpers.
- `trading-agent-skills-decisions` CLI with `append`, `migrate-to-sqlite`, `export-jsonl` subcommands.
- SQLite `decisions` table (single wide table with promoted columns + JSON payload + sha256 dedup_key).

### Changed
- `decision_log.write_intent` / `write_outcome` now route through `decisions_io.append` (transparent — same JSONL contract, plus SQLite mirror).
- `decision_log.reconcile_decisions` / `filter_decisions` now consume `decisions_io.read_raw` (SQLite-first with JSONL fallback).

### Migration
- Run `trading-agent-skills-decisions migrate-to-sqlite --decisions-path <jsonl>` once per account. Idempotent.
- JSONL remains canonical during a dual-write window; weekend cutover scheduled 2026-05-16. Drift-check via `export-jsonl` + diff.

## 0.2.0 — 2026-05-11

### Added
- `journal_io` now dual-writes every entry to a sibling `trader.db` SQLite (Phase 1 of Option β JSONL → SQLite migration). JSONL remains canonical. All five record types are covered: `open`, `update`, `sl-trailed`, `partial-closed`, `closed`.
- New CLI subcommand `trading-agent-skills-journal migrate-to-sqlite --journal-path <path>` — idempotent backfill of historical JSONL into SQLite (all five tables). Idempotency via uuid PK + `UNIQUE(uuid, ts/update_time)` + `INSERT OR IGNORE`.
- New CLI subcommand `trading-agent-skills-journal export-jsonl --journal-path <path> --out <path>` — re-emits JSONL from the SQLite backing for human inspection.

### Changed
- `journal_io.read_raw` now prefers the SQLite backing when `trader.db` exists, with JSONL fallback. `read_resolved`, `read_resolved_with_events`, `filter_resolved`, `suggest_tags`, and `find_uuid_by_ticket` consume `read_raw` transparently — no caller changes needed.
- `account_paths.AccountPaths` gained a `db` field pointing at `trader.db` in the per-account dir.
