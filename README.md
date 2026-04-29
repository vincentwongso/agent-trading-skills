# cfd-trading-skills

Reasoning-layer Claude Code skills for CFD day trading on top of [`mt5-mcp`](https://github.com/vincentwongso/mt5-mcp) and [Calix](https://calix.fintrixmarkets.com).

Four skills (each lands incrementally — start with `cfd-position-sizer`):

1. **`cfd-position-sizer`** — Computes lot size for a target risk %, with broker-authoritative margin cross-check and swap-aware output.
2. **`daily-risk-guardian` + `pre-trade-checklist`** — Track today's P&L vs. configurable cap (NY 4pm ET reset), gate new trades against news / session / exposure / spread.
3. **`trade-journal`** — Append-only JSONL journal of completed trades with R-multiple, swap-accrued, and post-trade reflection.
4. **`session-news-brief`** — Dynamic watchlist + Calix calendar overlay + 3-API news fan-out + swing-candidates section (positive carry × technical extremes).

None of the skills mutate broker state — they advise, gate, or record. All execution stays behind `mt5-trading`'s existing consent flow.

See [`cfd-trading-skills-plan.md`](cfd-trading-skills-plan.md) for the full design.

## Layout

```
src/cfd_skills/        # pure-Python helpers (no I/O at the package boundary)
.claude/skills/        # one folder per skill (SKILL.md + thin scripts/ entry points)
tests/                 # pytest, no live broker required
```

## Development

```bash
python -m venv .venv
.venv/Scripts/activate    # Windows; `source .venv/bin/activate` elsewhere
pip install -e ".[dev]"
pytest
```

Skills consume MCP tool outputs via the Claude Code agent — there is no direct dependency on the `mt5_mcp` Python package. The agent calls MCP tools, and `cfd_skills` does the math on the resulting JSON.
