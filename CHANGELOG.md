# Changelog

All notable changes to this project will be documented here. Format roughly follows [Keep a Changelog](https://keepachangelog.com/), versioning follows [SemVer](https://semver.org/).

## 0.2.0 — 2026-05-11

### Added
- `journal_io` now dual-writes every entry to a sibling `trader.db` SQLite (Phase 1 of Option β JSONL → SQLite migration). JSONL remains canonical. All five record types are covered: `open`, `update`, `sl-trailed`, `partial-closed`, `closed`.
- New CLI subcommand `trading-agent-skills-journal migrate-to-sqlite --journal-path <path>` — idempotent backfill of historical JSONL into SQLite (all five tables). Idempotency via uuid PK + `UNIQUE(uuid, ts/update_time)` + `INSERT OR IGNORE`.
- New CLI subcommand `trading-agent-skills-journal export-jsonl --journal-path <path> --out <path>` — re-emits JSONL from the SQLite backing for human inspection.

### Changed
- `journal_io.read_raw` now prefers the SQLite backing when `trader.db` exists, with JSONL fallback. `read_resolved`, `read_resolved_with_events`, `filter_resolved`, `suggest_tags`, and `find_uuid_by_ticket` consume `read_raw` transparently — no caller changes needed.
- `account_paths.AccountPaths` gained a `db` field pointing at `trader.db` in the per-account dir.
