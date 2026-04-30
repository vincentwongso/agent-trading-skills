# Autonomous Trading Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build heartbeat-based autonomous demo trading on top of the existing six advisory skills, with mandatory reasoning capture per action and a weekly user-gated strategy review.

**Architecture:** Three Python modules (`account_paths`, `charter_io`, `decision_log`), one analytics module (`strategy_review`), two new markdown skills (`trading-heartbeat`, `strategy-review`), and a `decision` subcommand added to the existing journal CLI. State is namespaced per-account under `~/.trading-agent-skills/accounts/<account_id>/` so account changes are clean-slate.

**Tech Stack:** Python 3.11+, pytest, Decimal (no floats), append-only JSONL, YAML for charter, mt5-mcp via the existing skills.

**Spec:** `docs/superpowers/specs/2026-04-30-autonomous-trading-loop-design.md`

---

## File Structure

### New Python modules
- `src/trading_agent_skills/account_paths.py` — per-account namespace resolver
- `src/trading_agent_skills/charter_io.py` — charter YAML parse/validate/write/archive
- `src/trading_agent_skills/decision_log.py` — decisions.jsonl schema, write, read, reconcile
- `src/trading_agent_skills/strategy_review.py` — performance summary, proposal generator, apply
- `src/trading_agent_skills/cli/strategy_review.py` — CLI for strategy-review skill

### Modified Python modules
- `src/trading_agent_skills/cli/journal.py` — add `decision write` / `decision read` subcommands
- `src/trading_agent_skills/journal_io.py` — accept optional `account_id` for path resolution
- `src/trading_agent_skills/daily_state.py` — accept optional `account_id` for path resolution

### New skills (markdown)
- `.claude/skills/trading-heartbeat/SKILL.md` — orchestrator (no Python)
- `.claude/skills/strategy-review/SKILL.md` — wraps the strategy-review CLI

### New tests
- `tests/test_account_paths.py`
- `tests/test_charter_io.py`
- `tests/test_decision_log.py`
- `tests/test_strategy_review.py`
- `tests/test_cli_strategy_review.py`
- New cases added to `tests/test_cli_journal.py` for the `decision` subcommand

### Modified docs
- `AGENTS.md` — add charter Q&A and demo→live runbook
- `CLAUDE.md` — bump status section
- `pyproject.toml` — add `trading-agent-skills-strategy-review` entry point
- New: `FUTURE.md` — track mt5-mcp backtest TODO

---

## Phase 1 — Foundation: per-account state + charter

### Task 1: `account_paths` module

**Files:**
- Create: `src/trading_agent_skills/account_paths.py`
- Test: `tests/test_account_paths.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_account_paths.py
from pathlib import Path

import pytest

from trading_agent_skills.account_paths import AccountPaths, resolve_account_paths


def test_resolve_paths_returns_namespaced_dirs(tmp_path: Path) -> None:
    paths = resolve_account_paths(account_id="12345678", base=tmp_path)
    assert paths.root == tmp_path / "accounts" / "12345678"
    assert paths.charter == paths.root / "charter.md"
    assert paths.charter_versions == paths.root / "charter_versions"
    assert paths.journal == paths.root / "journal.jsonl"
    assert paths.decisions == paths.root / "decisions.jsonl"
    assert paths.proposals == paths.root / "proposals"
    assert paths.daily_state == paths.root / "daily_state.json"


def test_resolve_paths_rejects_blank_account_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="account_id"):
        resolve_account_paths(account_id="", base=tmp_path)


def test_resolve_paths_rejects_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="account_id"):
        resolve_account_paths(account_id="../etc", base=tmp_path)


def test_ensure_dirs_creates_root_versions_proposals(tmp_path: Path) -> None:
    paths = resolve_account_paths(account_id="12345678", base=tmp_path)
    paths.ensure_dirs()
    assert paths.root.is_dir()
    assert paths.charter_versions.is_dir()
    assert paths.proposals.is_dir()


def test_default_base_is_trading_agent_skills_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    paths = resolve_account_paths(account_id="12345678")
    assert paths.root == Path.home() / ".trading-agent-skills" / "accounts" / "12345678"
```

- [ ] **Step 2: Run test to verify it fails**

```
./.venv/Scripts/python.exe -m pytest tests/test_account_paths.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'trading_agent_skills.account_paths'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/trading_agent_skills/account_paths.py
"""Per-account-id state path resolver.

Each MT5 account gets its own namespace under ~/.trading-agent-skills/accounts/<id>/
so account changes are a clean-slate operation by design.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


_VALID_ACCOUNT_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_DEFAULT_BASE = Path.home() / ".trading-agent-skills"


@dataclass(frozen=True)
class AccountPaths:
    root: Path
    charter: Path
    charter_versions: Path
    journal: Path
    decisions: Path
    proposals: Path
    daily_state: Path

    def ensure_dirs(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.charter_versions.mkdir(exist_ok=True)
        self.proposals.mkdir(exist_ok=True)


def resolve_account_paths(*, account_id: str, base: Optional[Path] = None) -> AccountPaths:
    if not account_id or not _VALID_ACCOUNT_ID.match(account_id):
        raise ValueError(
            f"account_id must be non-empty alphanumeric (with - or _), got {account_id!r}"
        )
    root = (base or _DEFAULT_BASE) / "accounts" / account_id
    return AccountPaths(
        root=root,
        charter=root / "charter.md",
        charter_versions=root / "charter_versions",
        journal=root / "journal.jsonl",
        decisions=root / "decisions.jsonl",
        proposals=root / "proposals",
        daily_state=root / "daily_state.json",
    )
```

- [ ] **Step 4: Run tests, verify they pass**

```
./.venv/Scripts/python.exe -m pytest tests/test_account_paths.py -v
```
Expected: 5 PASSED

- [ ] **Step 5: Commit**

```
rtk git add src/trading_agent_skills/account_paths.py tests/test_account_paths.py
rtk git commit -m "feat(account-paths): per-account state namespace resolver"
```

---

### Task 2: `charter_io` — parse + validate

**Files:**
- Create: `src/trading_agent_skills/charter_io.py`
- Test: `tests/test_charter_io.py`

The charter is a YAML-like markdown — we use a small hand-rolled parser (no PyYAML dependency to keep the install footprint small; charter is fixed-shape so a simple parser is fine).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_charter_io.py
from pathlib import Path

import pytest

from trading_agent_skills.charter_io import (
    Charter,
    CharterError,
    HEARTBEAT_BY_STYLE,
    LOCKED_FIELDS,
    parse_charter,
)


_VALID_CHARTER = """\
mode: demo
account_id: 12345678
heartbeat: 1h
hard_caps:
  per_trade_risk_pct: 1.0
  daily_loss_pct: 5.0
  max_concurrent_positions: 3
charter_version: 1
created_at: 2026-04-30T14:00:00+10:00
created_account_balance: 10000.00
trading_style: day
sessions_allowed: []
instruments: []
allowed_setups: []
notes: ""
"""


def test_parses_minimal_valid_charter() -> None:
    c = parse_charter(_VALID_CHARTER)
    assert c.mode == "demo"
    assert c.account_id == "12345678"
    assert c.heartbeat == "1h"
    assert c.hard_caps.per_trade_risk_pct == 1.0
    assert c.hard_caps.daily_loss_pct == 5.0
    assert c.hard_caps.max_concurrent_positions == 3
    assert c.charter_version == 1
    assert c.trading_style == "day"
    assert c.sessions_allowed == []
    assert c.instruments == []
    assert c.allowed_setups == []
    assert c.notes == ""


def test_rejects_invalid_mode() -> None:
    bad = _VALID_CHARTER.replace("mode: demo", "mode: yolo")
    with pytest.raises(CharterError, match="mode"):
        parse_charter(bad)


def test_rejects_per_trade_risk_above_5pct() -> None:
    bad = _VALID_CHARTER.replace("per_trade_risk_pct: 1.0", "per_trade_risk_pct: 6.0")
    with pytest.raises(CharterError, match="per_trade_risk_pct"):
        parse_charter(bad)


def test_rejects_daily_loss_above_20pct() -> None:
    bad = _VALID_CHARTER.replace("daily_loss_pct: 5.0", "daily_loss_pct: 21.0")
    with pytest.raises(CharterError, match="daily_loss_pct"):
        parse_charter(bad)


def test_rejects_invalid_heartbeat() -> None:
    bad = _VALID_CHARTER.replace("heartbeat: 1h", "heartbeat: 1day")
    with pytest.raises(CharterError, match="heartbeat"):
        parse_charter(bad)


def test_warns_on_style_heartbeat_mismatch() -> None:
    bad = _VALID_CHARTER.replace("heartbeat: 1h", "heartbeat: 4h").replace(
        "trading_style: day", "trading_style: scalp"
    )
    with pytest.raises(CharterError, match="trading_style"):
        parse_charter(bad)


def test_locked_fields_constants() -> None:
    assert "mode" in LOCKED_FIELDS
    assert "account_id" in LOCKED_FIELDS
    assert "created_at" in LOCKED_FIELDS
    assert "created_account_balance" in LOCKED_FIELDS
    assert "charter_version" in LOCKED_FIELDS
    assert "per_trade_risk_pct" not in LOCKED_FIELDS  # proposable


def test_heartbeat_defaults_by_style() -> None:
    assert HEARTBEAT_BY_STYLE["scalp"] == "15m"
    assert HEARTBEAT_BY_STYLE["day"] == "1h"
    assert HEARTBEAT_BY_STYLE["swing"] == "4h"


def test_rejects_missing_required_field() -> None:
    bad = _VALID_CHARTER.replace("mode: demo\n", "")
    with pytest.raises(CharterError, match="mode"):
        parse_charter(bad)
```

- [ ] **Step 2: Run test, verify failure**

```
./.venv/Scripts/python.exe -m pytest tests/test_charter_io.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'trading_agent_skills.charter_io'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/trading_agent_skills/charter_io.py
"""Operating-charter YAML parser + validator.

Charter shape is fixed; we hand-roll a small parser to avoid PyYAML.
Keep this strict — bad data here silently changes trading behavior.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List


HEARTBEAT_BY_STYLE = {"scalp": "15m", "day": "1h", "swing": "4h"}
ALLOWED_HEARTBEATS = {"5m", "10m", "15m", "30m", "1h", "2h", "4h"}
ALLOWED_MODES = {"demo", "live"}
ALLOWED_STYLES = {"scalp", "day", "swing"}
ALLOWED_SESSIONS = {"tokyo", "london", "ny"}

# Style → set of acceptable heartbeats (per spec §5.1)
STYLE_HEARTBEAT_RANGES = {
    "scalp": {"5m", "10m", "15m"},
    "day": {"30m", "1h"},
    "swing": {"1h", "2h", "4h"},
}

LOCKED_FIELDS = frozenset(
    {"mode", "account_id", "created_at", "created_account_balance", "charter_version"}
)


class CharterError(ValueError):
    """Charter content violates the required shape or value bounds."""


@dataclass(frozen=True)
class HardCaps:
    per_trade_risk_pct: float
    daily_loss_pct: float
    max_concurrent_positions: int


@dataclass(frozen=True)
class Charter:
    mode: str
    account_id: str
    heartbeat: str
    hard_caps: HardCaps
    charter_version: int
    created_at: str
    created_account_balance: float
    trading_style: str
    sessions_allowed: List[str] = field(default_factory=list)
    instruments: List[str] = field(default_factory=list)
    allowed_setups: List[str] = field(default_factory=list)
    notes: str = ""


_TOP_LEVEL_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$")
_NESTED_RE = re.compile(r"^\s{2,}([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$")


def parse_charter(text: str) -> Charter:
    fields_top: dict[str, str] = {}
    hard_caps_raw: dict[str, str] = {}

    in_hard_caps = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line.startswith("hard_caps:"):
            in_hard_caps = True
            continue
        nested = _NESTED_RE.match(line)
        if nested and in_hard_caps:
            hard_caps_raw[nested.group(1)] = nested.group(2).strip()
            continue
        in_hard_caps = False
        m = _TOP_LEVEL_RE.match(line)
        if not m:
            continue
        fields_top[m.group(1)] = m.group(2).strip()

    return _build_charter(fields_top, hard_caps_raw)


def _build_charter(top: dict[str, str], hc: dict[str, str]) -> Charter:
    required = {"mode", "account_id", "heartbeat", "charter_version", "created_at",
                "created_account_balance", "trading_style"}
    for key in required:
        if key not in top:
            raise CharterError(f"missing required field: {key}")
    for key in ("per_trade_risk_pct", "daily_loss_pct", "max_concurrent_positions"):
        if key not in hc:
            raise CharterError(f"missing required hard_caps.{key}")

    mode = top["mode"]
    if mode not in ALLOWED_MODES:
        raise CharterError(f"mode must be one of {ALLOWED_MODES}, got {mode!r}")

    heartbeat = top["heartbeat"]
    if heartbeat not in ALLOWED_HEARTBEATS:
        raise CharterError(f"heartbeat must be one of {ALLOWED_HEARTBEATS}, got {heartbeat!r}")

    style = top["trading_style"]
    if style not in ALLOWED_STYLES:
        raise CharterError(f"trading_style must be one of {ALLOWED_STYLES}, got {style!r}")

    if heartbeat not in STYLE_HEARTBEAT_RANGES[style]:
        raise CharterError(
            f"trading_style={style!r} requires heartbeat in {STYLE_HEARTBEAT_RANGES[style]}, "
            f"got heartbeat={heartbeat!r}"
        )

    per_trade = float(hc["per_trade_risk_pct"])
    if not 0 < per_trade <= 5.0:
        raise CharterError(f"per_trade_risk_pct must be in (0, 5.0], got {per_trade}")
    daily_loss = float(hc["daily_loss_pct"])
    if not 0 < daily_loss <= 20.0:
        raise CharterError(f"daily_loss_pct must be in (0, 20.0], got {daily_loss}")
    max_conc = int(hc["max_concurrent_positions"])
    if not 1 <= max_conc <= 20:
        raise CharterError(
            f"max_concurrent_positions must be in [1, 20], got {max_conc}"
        )

    sessions = _parse_list(top.get("sessions_allowed", "[]"))
    for s in sessions:
        if s not in ALLOWED_SESSIONS:
            raise CharterError(f"sessions_allowed[] entry {s!r} not in {ALLOWED_SESSIONS}")

    return Charter(
        mode=mode,
        account_id=top["account_id"],
        heartbeat=heartbeat,
        hard_caps=HardCaps(
            per_trade_risk_pct=per_trade,
            daily_loss_pct=daily_loss,
            max_concurrent_positions=max_conc,
        ),
        charter_version=int(top["charter_version"]),
        created_at=top["created_at"],
        created_account_balance=float(top["created_account_balance"]),
        trading_style=style,
        sessions_allowed=sessions,
        instruments=_parse_list(top.get("instruments", "[]")),
        allowed_setups=_parse_list(top.get("allowed_setups", "[]")),
        notes=_strip_quotes(top.get("notes", '""')),
    )


def _parse_list(raw: str) -> List[str]:
    """Parse `[]` or `["a", "b"]` or `[a, b]` into a list."""
    raw = raw.strip()
    if raw == "[]" or raw == "":
        return []
    if not (raw.startswith("[") and raw.endswith("]")):
        raise CharterError(f"expected list literal, got {raw!r}")
    inner = raw[1:-1].strip()
    if not inner:
        return []
    return [_strip_quotes(item.strip()) for item in inner.split(",")]


def _strip_quotes(raw: str) -> str:
    raw = raw.strip()
    if (raw.startswith('"') and raw.endswith('"')) or (
        raw.startswith("'") and raw.endswith("'")
    ):
        return raw[1:-1]
    return raw
```

- [ ] **Step 4: Run tests**

```
./.venv/Scripts/python.exe -m pytest tests/test_charter_io.py -v
```
Expected: 9 PASSED

- [ ] **Step 5: Commit**

```
rtk git add src/trading_agent_skills/charter_io.py tests/test_charter_io.py
rtk git commit -m "feat(charter-io): parse and validate operating charter"
```

---

### Task 3: `charter_io` — write + version archival

**Files:**
- Modify: `src/trading_agent_skills/charter_io.py`
- Modify: `tests/test_charter_io.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_charter_io.py`:

```python
from trading_agent_skills.account_paths import resolve_account_paths
from trading_agent_skills.charter_io import (
    render_charter,
    write_charter,
    write_charter_with_archive,
)


def test_render_charter_round_trips(tmp_path: Path) -> None:
    c = parse_charter(_VALID_CHARTER)
    rendered = render_charter(c)
    assert "mode: demo" in rendered
    assert "account_id: 12345678" in rendered
    assert "heartbeat: 1h" in rendered
    assert "per_trade_risk_pct: 1.0" in rendered
    re_parsed = parse_charter(rendered)
    assert re_parsed == c


def test_write_charter_creates_file(tmp_path: Path) -> None:
    paths = resolve_account_paths(account_id="12345678", base=tmp_path)
    paths.ensure_dirs()
    c = parse_charter(_VALID_CHARTER)
    write_charter(paths.charter, c)
    assert paths.charter.is_file()
    assert "mode: demo" in paths.charter.read_text(encoding="utf-8")


def test_archive_old_before_overwrite(tmp_path: Path) -> None:
    paths = resolve_account_paths(account_id="12345678", base=tmp_path)
    paths.ensure_dirs()
    v1 = parse_charter(_VALID_CHARTER)
    write_charter(paths.charter, v1)

    v2_text = _VALID_CHARTER.replace("charter_version: 1", "charter_version: 2").replace(
        "per_trade_risk_pct: 1.0", "per_trade_risk_pct: 0.8"
    )
    v2 = parse_charter(v2_text)
    write_charter_with_archive(paths, v2)

    assert (paths.charter_versions / "v1.md").is_file()
    assert "per_trade_risk_pct: 1.0" in (paths.charter_versions / "v1.md").read_text(
        encoding="utf-8"
    )
    assert "per_trade_risk_pct: 0.8" in paths.charter.read_text(encoding="utf-8")


def test_archive_refuses_version_mismatch(tmp_path: Path) -> None:
    paths = resolve_account_paths(account_id="12345678", base=tmp_path)
    paths.ensure_dirs()
    v1 = parse_charter(_VALID_CHARTER)
    write_charter(paths.charter, v1)
    # Re-write at version 1 (no bump) — must refuse
    with pytest.raises(CharterError, match="must increment"):
        write_charter_with_archive(paths, v1)
```

- [ ] **Step 2: Run tests, verify failure**

```
./.venv/Scripts/python.exe -m pytest tests/test_charter_io.py -v
```
Expected: 4 NEW failures (`render_charter`, `write_charter`, `write_charter_with_archive` not defined)

- [ ] **Step 3: Add implementation**

Append to `src/trading_agent_skills/charter_io.py`:

```python
from trading_agent_skills.account_paths import AccountPaths


def render_charter(c: Charter) -> str:
    """Render a Charter back to the YAML-like text format parse_charter consumes."""
    sessions = "[" + ", ".join(f'"{s}"' for s in c.sessions_allowed) + "]" if c.sessions_allowed else "[]"
    instruments = "[" + ", ".join(f'"{s}"' for s in c.instruments) + "]" if c.instruments else "[]"
    setups = "[" + ", ".join(f'"{s}"' for s in c.allowed_setups) + "]" if c.allowed_setups else "[]"
    return (
        f"mode: {c.mode}\n"
        f"account_id: {c.account_id}\n"
        f"heartbeat: {c.heartbeat}\n"
        f"hard_caps:\n"
        f"  per_trade_risk_pct: {c.hard_caps.per_trade_risk_pct}\n"
        f"  daily_loss_pct: {c.hard_caps.daily_loss_pct}\n"
        f"  max_concurrent_positions: {c.hard_caps.max_concurrent_positions}\n"
        f"charter_version: {c.charter_version}\n"
        f"created_at: {c.created_at}\n"
        f"created_account_balance: {c.created_account_balance}\n"
        f"trading_style: {c.trading_style}\n"
        f"sessions_allowed: {sessions}\n"
        f"instruments: {instruments}\n"
        f"allowed_setups: {setups}\n"
        f'notes: "{c.notes}"\n'
    )


def write_charter(path: Path, c: Charter) -> None:
    path.write_text(render_charter(c), encoding="utf-8")


def write_charter_with_archive(paths: AccountPaths, new: Charter) -> None:
    """Archive the current charter to charter_versions/v<N>.md, then overwrite.

    Refuses if new.charter_version is not strictly greater than the current
    on-disk version. Caller is responsible for bumping charter_version.
    """
    if paths.charter.is_file():
        old = parse_charter(paths.charter.read_text(encoding="utf-8"))
        if new.charter_version <= old.charter_version:
            raise CharterError(
                f"new charter must increment charter_version above {old.charter_version}, "
                f"got {new.charter_version}"
            )
        archive_path = paths.charter_versions / f"v{old.charter_version}.md"
        archive_path.write_text(render_charter(old), encoding="utf-8")
    write_charter(paths.charter, new)
```

- [ ] **Step 4: Run tests**

```
./.venv/Scripts/python.exe -m pytest tests/test_charter_io.py -v
```
Expected: 13 PASSED

- [ ] **Step 5: Commit**

```
rtk git add src/trading_agent_skills/charter_io.py tests/test_charter_io.py
rtk git commit -m "feat(charter-io): write and archive charter versions on update"
```

---

## Phase 2 — Decision log

### Task 4: `decision_log` schema + intent writer

**Files:**
- Create: `src/trading_agent_skills/decision_log.py`
- Test: `tests/test_decision_log.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_decision_log.py
import json
from pathlib import Path

import pytest

from trading_agent_skills.decision_log import (
    ALLOWED_KINDS,
    ALLOWED_EXEC_STATUSES,
    DecisionSchemaError,
    write_intent,
)


def test_write_intent_open_appends_record(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    write_intent(
        path,
        kind="open",
        symbol="XAUUSD.z",
        ticket=None,
        setup_type="price_action:pin_bar",
        reasoning="Pullback to 2380, pin bar rejection on H1.",
        skills_used=["price-action", "pre-trade-checklist", "position-sizer"],
        guardian_status="CLEAR",
        checklist_verdict="PASS",
        execution={
            "side": "BUY",
            "volume": "0.08",
            "entry_price": "2380.50",
            "sl": "2375.00",
            "tp": "2395.00",
        },
        charter_version=3,
        tick_id="2026-04-30T22:00:00Z",
    )
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["kind"] == "open"
    assert rec["symbol"] == "XAUUSD.z"
    assert rec["execution"]["execution_status"] == "pending"
    assert rec["execution"]["volume"] == "0.08"  # string, not float
    assert rec["charter_version"] == 3
    assert rec["tick_id"] == "2026-04-30T22:00:00Z"


def test_write_intent_skip_no_execution(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    write_intent(
        path,
        kind="skip",
        symbol="EURUSD.z",
        ticket=None,
        setup_type="price_action:fvg_fill",
        reasoning="Spread 1.8x baseline, skipped.",
        skills_used=["price-action", "pre-trade-checklist"],
        guardian_status="CAUTION",
        checklist_verdict="BLOCK",
        execution=None,
        charter_version=3,
        tick_id="2026-04-30T22:00:00Z",
    )
    rec = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert rec["kind"] == "skip"
    assert rec["execution"] is None


def test_write_intent_rejects_unknown_kind(tmp_path: Path) -> None:
    with pytest.raises(DecisionSchemaError, match="kind"):
        write_intent(
            tmp_path / "d.jsonl",
            kind="explode",
            symbol="X",
            ticket=None,
            setup_type="x",
            reasoning="r",
            skills_used=[],
            guardian_status="CLEAR",
            checklist_verdict=None,
            execution=None,
            charter_version=1,
            tick_id="2026-04-30T22:00:00Z",
        )


def test_write_intent_rejects_open_without_execution(tmp_path: Path) -> None:
    with pytest.raises(DecisionSchemaError, match="execution"):
        write_intent(
            tmp_path / "d.jsonl",
            kind="open",
            symbol="X",
            ticket=None,
            setup_type="x",
            reasoning="r",
            skills_used=[],
            guardian_status="CLEAR",
            checklist_verdict="PASS",
            execution=None,
            charter_version=1,
            tick_id="2026-04-30T22:00:00Z",
        )


def test_write_intent_rejects_open_without_setup_type(tmp_path: Path) -> None:
    with pytest.raises(DecisionSchemaError, match="setup_type"):
        write_intent(
            tmp_path / "d.jsonl",
            kind="open",
            symbol="X",
            ticket=None,
            setup_type="",
            reasoning="r",
            skills_used=[],
            guardian_status="CLEAR",
            checklist_verdict="PASS",
            execution={
                "side": "BUY", "volume": "0.1", "entry_price": "1.0",
                "sl": "0.99", "tp": "1.02",
            },
            charter_version=1,
            tick_id="2026-04-30T22:00:00Z",
        )


def test_write_intent_rejects_naive_tick_id(tmp_path: Path) -> None:
    with pytest.raises(DecisionSchemaError, match="tick_id"):
        write_intent(
            tmp_path / "d.jsonl",
            kind="skip",
            symbol="X",
            ticket=None,
            setup_type="x",
            reasoning="r",
            skills_used=[],
            guardian_status="CLEAR",
            checklist_verdict="BLOCK",
            execution=None,
            charter_version=1,
            tick_id="2026-04-30T22:00:00",  # missing Z / +00:00
        )


def test_write_intent_rejects_volume_as_float(tmp_path: Path) -> None:
    with pytest.raises(DecisionSchemaError, match="volume"):
        write_intent(
            tmp_path / "d.jsonl",
            kind="open",
            symbol="X",
            ticket=None,
            setup_type="x",
            reasoning="r",
            skills_used=[],
            guardian_status="CLEAR",
            checklist_verdict="PASS",
            execution={
                "side": "BUY", "volume": 0.08, "entry_price": "1.0",
                "sl": "0.99", "tp": "1.02",
            },
            charter_version=1,
            tick_id="2026-04-30T22:00:00Z",
        )


def test_allowed_constants() -> None:
    assert ALLOWED_KINDS == ("open", "modify", "close", "skip", "mode_change")
    assert "pending" in ALLOWED_EXEC_STATUSES
    assert "filled" in ALLOWED_EXEC_STATUSES
    assert "rejected" in ALLOWED_EXEC_STATUSES
    assert "broker_error" in ALLOWED_EXEC_STATUSES
```

- [ ] **Step 2: Run, verify failure**

```
./.venv/Scripts/python.exe -m pytest tests/test_decision_log.py -v
```
Expected: 8 FAIL (module not found)

- [ ] **Step 3: Implement**

```python
# src/trading_agent_skills/decision_log.py
"""Append-only decisions.jsonl — every executed action OR evaluated-but-skipped candidate.

Schema is version-tagged. Records are written in two phases:
  1. Intent record with execution.execution_status = "pending" BEFORE broker call
  2. Outcome record with same (tick_id, kind, symbol) updating execution_status
     to filled / rejected / broker_error AFTER broker call

Reader joins on (tick_id, kind, symbol), latest-by-ts wins for execution state;
reasoning from the intent record is canonical.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, List, Literal, Optional


SCHEMA_VERSION = 1
ALLOWED_KINDS = ("open", "modify", "close", "skip", "mode_change")
ALLOWED_EXEC_STATUSES = ("pending", "filled", "rejected", "broker_error")
ALLOWED_GUARDIAN = ("CLEAR", "CAUTION", "HALT")
ALLOWED_CHECKLIST = ("PASS", "WARN", "BLOCK", None)
ALLOWED_SIDES = ("BUY", "SELL")

_DECIMAL_EXEC_FIELDS = ("volume", "entry_price", "sl", "tp")


class DecisionSchemaError(ValueError):
    """A decision record violates the required schema."""


def _validate_tick_id(tick_id: str) -> None:
    if not isinstance(tick_id, str):
        raise DecisionSchemaError(f"tick_id must be a string, got {type(tick_id).__name__}")
    try:
        dt = datetime.fromisoformat(tick_id.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DecisionSchemaError(f"tick_id: invalid ISO 8601 — {exc}") from exc
    if dt.tzinfo is None:
        raise DecisionSchemaError("tick_id: must be timezone-aware (Z or offset suffix)")


def _validate_execution_block(execution: dict[str, Any]) -> None:
    if not isinstance(execution, dict):
        raise DecisionSchemaError("execution must be a dict")
    if execution.get("side") not in ALLOWED_SIDES:
        raise DecisionSchemaError(
            f"execution.side must be in {ALLOWED_SIDES}, got {execution.get('side')!r}"
        )
    for field in _DECIMAL_EXEC_FIELDS:
        v = execution.get(field)
        if not isinstance(v, str) or not v:
            raise DecisionSchemaError(
                f"execution.{field} must be a non-empty string (Decimal-as-string), got {v!r}"
            )


def write_intent(
    path: Path,
    *,
    kind: str,
    symbol: str,
    ticket: Optional[int],
    setup_type: str,
    reasoning: str,
    skills_used: List[str],
    guardian_status: str,
    checklist_verdict: Optional[str],
    execution: Optional[dict[str, Any]],
    charter_version: int,
    tick_id: str,
) -> dict[str, Any]:
    if kind not in ALLOWED_KINDS:
        raise DecisionSchemaError(f"kind must be in {ALLOWED_KINDS}, got {kind!r}")
    if guardian_status not in ALLOWED_GUARDIAN:
        raise DecisionSchemaError(
            f"guardian_status must be in {ALLOWED_GUARDIAN}, got {guardian_status!r}"
        )
    if checklist_verdict not in ALLOWED_CHECKLIST:
        raise DecisionSchemaError(
            f"checklist_verdict must be in {ALLOWED_CHECKLIST}, got {checklist_verdict!r}"
        )
    if not symbol:
        raise DecisionSchemaError("symbol is required")
    if not reasoning:
        raise DecisionSchemaError("reasoning is required")
    if kind in ("open", "skip") and not setup_type:
        raise DecisionSchemaError(f"setup_type is required for kind={kind!r}")
    if kind in ("open", "modify", "close") and execution is None:
        raise DecisionSchemaError(f"execution is required for kind={kind!r}")
    if execution is not None:
        _validate_execution_block(execution)
        execution = {**execution, "execution_status": "pending"}
    _validate_tick_id(tick_id)

    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "symbol": symbol,
        "ticket": ticket,
        "setup_type": setup_type or None,
        "reasoning": reasoning,
        "skills_used": list(skills_used),
        "guardian_status": guardian_status,
        "checklist_verdict": checklist_verdict,
        "execution": execution,
        "charter_version": charter_version,
        "tick_id": tick_id,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return record
```

- [ ] **Step 4: Run tests**

```
./.venv/Scripts/python.exe -m pytest tests/test_decision_log.py -v
```
Expected: 8 PASSED

- [ ] **Step 5: Commit**

```
rtk git add src/trading_agent_skills/decision_log.py tests/test_decision_log.py
rtk git commit -m "feat(decision-log): intent record schema and write-before-execute"
```

---

### Task 5: `decision_log` outcome writer + reconciliation

**Files:**
- Modify: `src/trading_agent_skills/decision_log.py`
- Modify: `tests/test_decision_log.py`

- [ ] **Step 1: Add failing tests**

```python
# Append to tests/test_decision_log.py
from trading_agent_skills.decision_log import (
    reconcile_decisions,
    write_outcome,
)


def test_outcome_pending_to_filled(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    write_intent(
        path, kind="open", symbol="XAUUSD.z", ticket=None,
        setup_type="price_action:pin_bar", reasoning="r", skills_used=[],
        guardian_status="CLEAR", checklist_verdict="PASS",
        execution={"side": "BUY", "volume": "0.08",
                   "entry_price": "2380.50", "sl": "2375.00", "tp": "2395.00"},
        charter_version=3, tick_id="2026-04-30T22:00:00Z",
    )
    write_outcome(
        path, tick_id="2026-04-30T22:00:00Z", kind="open", symbol="XAUUSD.z",
        execution_status="filled", ticket=99999,
        actual_fill_price="2380.55", failure_reason=None,
    )
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    intent = json.loads(lines[0])
    outcome = json.loads(lines[1])
    assert intent["execution"]["execution_status"] == "pending"
    assert outcome["execution"]["execution_status"] == "filled"
    assert outcome["ticket"] == 99999


def test_outcome_rejected_with_reason(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    write_intent(
        path, kind="open", symbol="X", ticket=None, setup_type="x",
        reasoning="r", skills_used=[], guardian_status="CLEAR", checklist_verdict="PASS",
        execution={"side": "BUY", "volume": "0.1", "entry_price": "1.0",
                   "sl": "0.99", "tp": "1.02"},
        charter_version=1, tick_id="2026-04-30T22:00:00Z",
    )
    write_outcome(
        path, tick_id="2026-04-30T22:00:00Z", kind="open", symbol="X",
        execution_status="rejected", ticket=None, actual_fill_price=None,
        failure_reason="market closed",
    )
    outcome = json.loads(path.read_text(encoding="utf-8").splitlines()[1])
    assert outcome["execution"]["execution_status"] == "rejected"
    assert outcome["execution"]["failure_reason"] == "market closed"


def test_outcome_rejects_invalid_status(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    with pytest.raises(DecisionSchemaError, match="execution_status"):
        write_outcome(
            path, tick_id="2026-04-30T22:00:00Z", kind="open", symbol="X",
            execution_status="totally_filled", ticket=1,
            actual_fill_price=None, failure_reason=None,
        )


def test_reconcile_picks_latest_outcome(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    write_intent(
        path, kind="open", symbol="XAUUSD.z", ticket=None, setup_type="x",
        reasoning="why", skills_used=[], guardian_status="CLEAR", checklist_verdict="PASS",
        execution={"side": "BUY", "volume": "0.1", "entry_price": "1.0",
                   "sl": "0.99", "tp": "1.02"},
        charter_version=1, tick_id="2026-04-30T22:00:00Z",
    )
    write_outcome(
        path, tick_id="2026-04-30T22:00:00Z", kind="open", symbol="XAUUSD.z",
        execution_status="filled", ticket=42, actual_fill_price="1.0001",
        failure_reason=None,
    )
    reconciled = list(reconcile_decisions(path))
    assert len(reconciled) == 1
    rec = reconciled[0]
    assert rec["reasoning"] == "why"
    assert rec["execution"]["execution_status"] == "filled"
    assert rec["ticket"] == 42


def test_reconcile_orphan_intent_stays_pending(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    write_intent(
        path, kind="open", symbol="X", ticket=None, setup_type="x",
        reasoning="r", skills_used=[], guardian_status="CLEAR", checklist_verdict="PASS",
        execution={"side": "BUY", "volume": "0.1", "entry_price": "1.0",
                   "sl": "0.99", "tp": "1.02"},
        charter_version=1, tick_id="2026-04-30T22:00:00Z",
    )
    reconciled = list(reconcile_decisions(path))
    assert reconciled[0]["execution"]["execution_status"] == "pending"
```

- [ ] **Step 2: Run, verify failure**

```
./.venv/Scripts/python.exe -m pytest tests/test_decision_log.py -v
```
Expected: 5 NEW failures

- [ ] **Step 3: Implement**

Append to `src/trading_agent_skills/decision_log.py`:

```python
def write_outcome(
    path: Path,
    *,
    tick_id: str,
    kind: str,
    symbol: str,
    execution_status: str,
    ticket: Optional[int],
    actual_fill_price: Optional[str],
    failure_reason: Optional[str],
) -> dict[str, Any]:
    if execution_status not in ALLOWED_EXEC_STATUSES or execution_status == "pending":
        raise DecisionSchemaError(
            f"execution_status must be in {set(ALLOWED_EXEC_STATUSES) - {'pending'}}, "
            f"got {execution_status!r}"
        )
    if kind not in ALLOWED_KINDS:
        raise DecisionSchemaError(f"kind must be in {ALLOWED_KINDS}, got {kind!r}")
    _validate_tick_id(tick_id)
    if actual_fill_price is not None and not isinstance(actual_fill_price, str):
        raise DecisionSchemaError("actual_fill_price must be a string or None")

    execution: dict[str, Any] = {"execution_status": execution_status}
    if actual_fill_price is not None:
        execution["actual_fill_price"] = actual_fill_price
    if failure_reason is not None:
        execution["failure_reason"] = failure_reason

    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "symbol": symbol,
        "ticket": ticket,
        "execution": execution,
        "tick_id": tick_id,
        "is_outcome": True,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return record


def _read_records(path: Path) -> Iterable[dict[str, Any]]:
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def reconcile_decisions(path: Path) -> Iterable[dict[str, Any]]:
    """Yield one merged record per (tick_id, kind, symbol).

    Intent record is the base; outcome record (latest by ts) overrides
    execution dict and ticket. Orphan intents (no outcome yet) keep
    execution_status='pending'.
    """
    intents: dict[tuple[str, str, str], dict[str, Any]] = {}
    outcomes: dict[tuple[str, str, str], dict[str, Any]] = {}
    for rec in _read_records(path):
        key = (rec.get("tick_id"), rec.get("kind"), rec.get("symbol"))
        if rec.get("is_outcome"):
            existing = outcomes.get(key)
            if existing is None or rec["ts"] > existing["ts"]:
                outcomes[key] = rec
        else:
            intents[key] = rec

    for key, intent in intents.items():
        merged = dict(intent)
        outcome = outcomes.get(key)
        if outcome:
            merged_exec = dict(intent.get("execution") or {})
            merged_exec.update(outcome["execution"])
            merged["execution"] = merged_exec
            if outcome.get("ticket") is not None:
                merged["ticket"] = outcome["ticket"]
        yield merged
```

- [ ] **Step 4: Run tests**

```
./.venv/Scripts/python.exe -m pytest tests/test_decision_log.py -v
```
Expected: 13 PASSED

- [ ] **Step 5: Commit**

```
rtk git add src/trading_agent_skills/decision_log.py tests/test_decision_log.py
rtk git commit -m "feat(decision-log): outcome writer and intent/outcome reconciliation"
```

---

### Task 6: `decision_log` query API

**Files:**
- Modify: `src/trading_agent_skills/decision_log.py`
- Modify: `tests/test_decision_log.py`

- [ ] **Step 1: Add failing tests**

```python
# Append to tests/test_decision_log.py
from datetime import datetime, timedelta, timezone

from trading_agent_skills.decision_log import filter_decisions


def _seed_decisions(path: Path) -> None:
    base = "2026-04-30T22:00:00Z"
    for i, (kind, sym, setup) in enumerate([
        ("open", "XAUUSD.z", "price_action:pin_bar"),
        ("skip", "EURUSD.z", "price_action:fvg_fill"),
        ("close", "XAUUSD.z", None),
    ]):
        tick = f"2026-04-{29 + i}T22:00:00Z"
        kwargs = dict(
            kind=kind, symbol=sym, ticket=None if kind == "skip" else 100 + i,
            setup_type=setup, reasoning=f"r{i}", skills_used=[],
            guardian_status="CLEAR",
            checklist_verdict="PASS" if kind == "open" else ("BLOCK" if kind == "skip" else None),
            charter_version=1, tick_id=tick,
        )
        if kind == "skip":
            write_intent(path, execution=None, **kwargs)
        else:
            write_intent(
                path,
                execution={"side": "BUY", "volume": "0.1", "entry_price": "1.0",
                           "sl": "0.99", "tp": "1.02"},
                **kwargs,
            )


def test_filter_by_kind(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    _seed_decisions(path)
    skips = list(filter_decisions(path, kind="skip"))
    assert len(skips) == 1
    assert skips[0]["symbol"] == "EURUSD.z"


def test_filter_by_symbol(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    _seed_decisions(path)
    xau = list(filter_decisions(path, symbol="XAUUSD.z"))
    assert len(xau) == 2
    assert {r["kind"] for r in xau} == {"open", "close"}


def test_filter_since_filters_old(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    _seed_decisions(path)
    cutoff = datetime(2026, 4, 30, 0, 0, 0, tzinfo=timezone.utc)
    recent = list(filter_decisions(path, since=cutoff))
    # Three records seeded at 2026-04-29, 2026-04-30, 2026-05-01 — only 04-30 and 05-01 pass
    assert len(recent) == 2


def test_filter_combines_predicates(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    _seed_decisions(path)
    cutoff = datetime(2026, 4, 30, 0, 0, 0, tzinfo=timezone.utc)
    recent_xau = list(filter_decisions(path, since=cutoff, symbol="XAUUSD.z"))
    assert len(recent_xau) == 1
    assert recent_xau[0]["kind"] == "close"
```

- [ ] **Step 2: Run, verify failure**

```
./.venv/Scripts/python.exe -m pytest tests/test_decision_log.py -v
```
Expected: 4 NEW failures

- [ ] **Step 3: Implement**

Append to `src/trading_agent_skills/decision_log.py`:

```python
def filter_decisions(
    path: Path,
    *,
    since: Optional[datetime] = None,
    kind: Optional[str] = None,
    symbol: Optional[str] = None,
) -> Iterable[dict[str, Any]]:
    """Yield reconciled decisions matching all supplied predicates."""
    for rec in reconcile_decisions(path):
        if kind is not None and rec.get("kind") != kind:
            continue
        if symbol is not None and rec.get("symbol") != symbol:
            continue
        if since is not None:
            tick = rec.get("tick_id")
            if tick is None:
                continue
            tick_dt = datetime.fromisoformat(tick.replace("Z", "+00:00"))
            if tick_dt < since:
                continue
        yield rec
```

- [ ] **Step 4: Run tests**

```
./.venv/Scripts/python.exe -m pytest tests/test_decision_log.py -v
```
Expected: 17 PASSED

- [ ] **Step 5: Commit**

```
rtk git add src/trading_agent_skills/decision_log.py tests/test_decision_log.py
rtk git commit -m "feat(decision-log): filter API for since/kind/symbol predicates"
```

---

### Task 7: `journal` CLI — `decision write` subcommand

**Files:**
- Modify: `src/trading_agent_skills/cli/journal.py`
- Modify: `tests/test_cli_journal.py`

- [ ] **Step 1: Inspect current CLI structure**

Read `src/trading_agent_skills/cli/journal.py` to identify the subparser pattern (look for `argparse.ArgumentParser` and `add_subparsers`). New subcommand follows the same pattern.

- [ ] **Step 2: Add failing test**

```python
# Append to tests/test_cli_journal.py — find the existing imports and module-level helpers
import json
import subprocess
import sys
from pathlib import Path


def _run_decision_write(path: Path, payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "trading_agent_skills.cli.journal",
         "decision", "write", "--decisions-path", str(path)],
        input=json.dumps(payload), text=True, capture_output=True,
    )


def test_cli_decision_write_intent(tmp_path: Path) -> None:
    decisions = tmp_path / "decisions.jsonl"
    payload = {
        "kind": "open",
        "symbol": "XAUUSD.z",
        "ticket": None,
        "setup_type": "price_action:pin_bar",
        "reasoning": "FVG fill at 2380.",
        "skills_used": ["price-action", "pre-trade-checklist"],
        "guardian_status": "CLEAR",
        "checklist_verdict": "PASS",
        "execution": {
            "side": "BUY", "volume": "0.05", "entry_price": "2380.00",
            "sl": "2375.00", "tp": "2390.00",
        },
        "charter_version": 1,
        "tick_id": "2026-04-30T22:00:00Z",
    }
    res = _run_decision_write(decisions, payload)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["status"] == "ok"
    assert decisions.is_file()
    rec = json.loads(decisions.read_text().splitlines()[0])
    assert rec["kind"] == "open"


def test_cli_decision_write_outcome(tmp_path: Path) -> None:
    decisions = tmp_path / "decisions.jsonl"
    # Seed an intent
    _run_decision_write(decisions, {
        "kind": "open", "symbol": "X", "ticket": None,
        "setup_type": "x", "reasoning": "r", "skills_used": [],
        "guardian_status": "CLEAR", "checklist_verdict": "PASS",
        "execution": {"side": "BUY", "volume": "0.1", "entry_price": "1.0",
                      "sl": "0.99", "tp": "1.02"},
        "charter_version": 1, "tick_id": "2026-04-30T22:00:00Z",
    })
    # Outcome
    res = subprocess.run(
        [sys.executable, "-m", "trading_agent_skills.cli.journal",
         "decision", "write-outcome", "--decisions-path", str(decisions)],
        input=json.dumps({
            "tick_id": "2026-04-30T22:00:00Z",
            "kind": "open", "symbol": "X",
            "execution_status": "filled", "ticket": 1234,
            "actual_fill_price": "1.0001", "failure_reason": None,
        }),
        text=True, capture_output=True,
    )
    assert res.returncode == 0, res.stderr
    lines = decisions.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["execution"]["execution_status"] == "filled"


def test_cli_decision_write_invalid_payload_nonzero_exit(tmp_path: Path) -> None:
    decisions = tmp_path / "decisions.jsonl"
    res = _run_decision_write(decisions, {"kind": "explode"})  # missing fields too
    assert res.returncode != 0
    assert "kind" in res.stderr or "kind" in res.stdout
```

- [ ] **Step 3: Run, verify failure**

```
./.venv/Scripts/python.exe -m pytest tests/test_cli_journal.py -k decision -v
```
Expected: 3 FAILED (no `decision` subcommand)

- [ ] **Step 4: Add subcommand**

In `src/trading_agent_skills/cli/journal.py`, find the function building the argparse subparsers (e.g., `_build_parser()` or similar) and add:

```python
# Add this import at top
from trading_agent_skills.decision_log import (
    DecisionSchemaError,
    write_intent,
    write_outcome,
)


def _add_decision_subcommand(subparsers: "argparse._SubParsersAction") -> None:
    p = subparsers.add_parser("decision", help="Decision log read/write (autonomous mode).")
    sub = p.add_subparsers(dest="decision_action", required=True)

    write = sub.add_parser("write", help="Append a decision-intent record from JSON stdin.")
    write.add_argument("--decisions-path", type=Path, required=True)
    write.set_defaults(func=_cmd_decision_write_intent)

    outcome = sub.add_parser("write-outcome", help="Append an outcome record from JSON stdin.")
    outcome.add_argument("--decisions-path", type=Path, required=True)
    outcome.set_defaults(func=_cmd_decision_write_outcome)


def _cmd_decision_write_intent(args: argparse.Namespace) -> int:
    payload = json.load(sys.stdin)
    try:
        rec = write_intent(args.decisions_path, **payload)
    except (DecisionSchemaError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps({"status": "ok", "record": rec}))
    return 0


def _cmd_decision_write_outcome(args: argparse.Namespace) -> int:
    payload = json.load(sys.stdin)
    try:
        rec = write_outcome(args.decisions_path, **payload)
    except (DecisionSchemaError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps({"status": "ok", "record": rec}))
    return 0
```

Wire up in the existing `main()` parser-building flow by calling `_add_decision_subcommand(subparsers)` next to the other subcommands. Make sure the dispatch loop calls `args.func(args)` for the new subcommand.

- [ ] **Step 5: Run tests**

```
./.venv/Scripts/python.exe -m pytest tests/test_cli_journal.py -k decision -v
```
Expected: 3 PASSED

- [ ] **Step 6: Run full journal test file to ensure no regressions**

```
./.venv/Scripts/python.exe -m pytest tests/test_cli_journal.py -v
```
Expected: all PASSED

- [ ] **Step 7: Commit**

```
rtk git add src/trading_agent_skills/cli/journal.py tests/test_cli_journal.py
rtk git commit -m "feat(journal-cli): decision write/write-outcome subcommands"
```

---

### Task 8: `journal` CLI — `decision read` subcommand

**Files:**
- Modify: `src/trading_agent_skills/cli/journal.py`
- Modify: `tests/test_cli_journal.py`

- [ ] **Step 1: Add failing test**

```python
# Append to tests/test_cli_journal.py
def test_cli_decision_read_filters(tmp_path: Path) -> None:
    decisions = tmp_path / "decisions.jsonl"
    # Seed two intents
    for sym in ("XAUUSD.z", "EURUSD.z"):
        _run_decision_write(decisions, {
            "kind": "open", "symbol": sym, "ticket": None,
            "setup_type": "price_action:pin_bar", "reasoning": "r",
            "skills_used": [], "guardian_status": "CLEAR", "checklist_verdict": "PASS",
            "execution": {"side": "BUY", "volume": "0.1", "entry_price": "1.0",
                          "sl": "0.99", "tp": "1.02"},
            "charter_version": 1, "tick_id": "2026-04-30T22:00:00Z",
        })
    res = subprocess.run(
        [sys.executable, "-m", "trading_agent_skills.cli.journal",
         "decision", "read", "--decisions-path", str(decisions),
         "--symbol", "XAUUSD.z"],
        text=True, capture_output=True,
    )
    assert res.returncode == 0
    out = json.loads(res.stdout)
    assert len(out["records"]) == 1
    assert out["records"][0]["symbol"] == "XAUUSD.z"


def test_cli_decision_read_since(tmp_path: Path) -> None:
    decisions = tmp_path / "decisions.jsonl"
    for tick_day, sym in (("2026-04-25T00:00:00Z", "OLD"), ("2026-04-30T00:00:00Z", "NEW")):
        _run_decision_write(decisions, {
            "kind": "open", "symbol": sym, "ticket": None,
            "setup_type": "x", "reasoning": "r", "skills_used": [],
            "guardian_status": "CLEAR", "checklist_verdict": "PASS",
            "execution": {"side": "BUY", "volume": "0.1", "entry_price": "1.0",
                          "sl": "0.99", "tp": "1.02"},
            "charter_version": 1, "tick_id": tick_day,
        })
    res = subprocess.run(
        [sys.executable, "-m", "trading_agent_skills.cli.journal",
         "decision", "read", "--decisions-path", str(decisions),
         "--since", "2026-04-29T00:00:00Z"],
        text=True, capture_output=True,
    )
    assert res.returncode == 0
    out = json.loads(res.stdout)
    assert {r["symbol"] for r in out["records"]} == {"NEW"}
```

- [ ] **Step 2: Run, verify failure**

Expected: FAIL — `read` action not registered.

- [ ] **Step 3: Implement**

Append in `cli/journal.py`:

```python
from datetime import datetime, timezone

from trading_agent_skills.decision_log import filter_decisions


def _add_decision_read(sub: "argparse._SubParsersAction") -> None:
    read = sub.add_parser("read", help="Read reconciled decision records, JSON to stdout.")
    read.add_argument("--decisions-path", type=Path, required=True)
    read.add_argument("--since", type=str, default=None,
                      help="ISO 8601 cutoff; records older than this are excluded.")
    read.add_argument("--kind", type=str, default=None,
                      choices=["open", "modify", "close", "skip", "mode_change"])
    read.add_argument("--symbol", type=str, default=None)
    read.set_defaults(func=_cmd_decision_read)


def _cmd_decision_read(args: argparse.Namespace) -> int:
    since_dt = None
    if args.since:
        since_dt = datetime.fromisoformat(args.since.replace("Z", "+00:00"))
    records = list(filter_decisions(
        args.decisions_path, since=since_dt, kind=args.kind, symbol=args.symbol
    ))
    print(json.dumps({"records": records}))
    return 0
```

In `_add_decision_subcommand`, add a call to `_add_decision_read(sub)` right after the existing `write` and `write-outcome` registrations.

- [ ] **Step 4: Run tests**

```
./.venv/Scripts/python.exe -m pytest tests/test_cli_journal.py -k decision -v
```
Expected: 5 PASSED

- [ ] **Step 5: Commit**

```
rtk git add src/trading_agent_skills/cli/journal.py tests/test_cli_journal.py
rtk git commit -m "feat(journal-cli): decision read with since/kind/symbol filters"
```

---

## Phase 3 — Per-account journal/state path resolution

### Task 9: `journal_io` accepts `account_id` for path resolution

**Files:**
- Modify: `src/trading_agent_skills/journal_io.py`
- Modify: `tests/test_journal_io.py`

The existing journal callers pass an explicit `path`. We add a new helper `default_journal_path(account_id)` that resolves to either the per-account file or the legacy root file. Existing callers are unchanged (backward-compat).

- [ ] **Step 1: Add failing test**

```python
# Append to tests/test_journal_io.py
from trading_agent_skills.journal_io import default_journal_path


def test_default_path_with_account_id(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    path = default_journal_path(account_id="12345678")
    expected = tmp_path / ".trading-agent-skills" / "accounts" / "12345678" / "journal.jsonl"
    assert path == expected


def test_default_path_without_account_id_is_legacy(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    path = default_journal_path(account_id=None)
    expected = tmp_path / ".trading-agent-skills" / "journal.jsonl"
    assert path == expected
```

- [ ] **Step 2: Run, verify failure**

```
./.venv/Scripts/python.exe -m pytest tests/test_journal_io.py -k default_path -v
```
Expected: FAIL — `default_journal_path` not defined.

- [ ] **Step 3: Implement**

Append to `src/trading_agent_skills/journal_io.py`:

```python
def default_journal_path(account_id: Optional[str] = None) -> Path:
    """Resolve the journal path for an account_id, or the legacy root path.

    With account_id: ~/.trading-agent-skills/accounts/<id>/journal.jsonl
    Without: ~/.trading-agent-skills/journal.jsonl (backwards-compat for manual use)
    """
    base = Path.home() / ".trading-agent-skills"
    if account_id:
        from trading_agent_skills.account_paths import resolve_account_paths

        return resolve_account_paths(account_id=account_id).journal
    return base / "journal.jsonl"
```

- [ ] **Step 4: Run tests**

```
./.venv/Scripts/python.exe -m pytest tests/test_journal_io.py -v
```
Expected: all PASSED

- [ ] **Step 5: Wire into CLI — add `--account-id` to journal subcommands**

In `src/trading_agent_skills/cli/journal.py`, find the existing `write`, `update`, `read`, `stats` subcommand registrations. Add to each:

```python
sub_write.add_argument("--account-id", type=str, default=None,
                       help="If set, journal is read/written under accounts/<id>/journal.jsonl")
```

In each command function, derive the journal path:

```python
journal_path = args.journal_path or default_journal_path(account_id=args.account_id)
```

(Replace any hard-coded `DEFAULT_PATH` lookups with this pattern.)

- [ ] **Step 6: Add failing CLI test**

```python
# Append to tests/test_cli_journal.py
def test_cli_journal_account_id_routes_writes(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    payload = {
        "symbol": "XAUUSD.z", "side": "buy", "volume": "0.1",
        "entry_price": "2380.00", "exit_price": "2390.00",
        "entry_time": "2026-04-30T08:00:00+00:00",
        "exit_time": "2026-04-30T16:00:00+00:00",
        "original_stop_distance_points": 50,
        "original_risk_amount": "100.00", "realized_pnl": "100.00",
        "swap_accrued": "0.00", "commission": "0.00",
        "setup_type": "price_action:pin_bar", "rationale": "test",
        "risk_classification_at_close": "AT_RISK",
    }
    res = subprocess.run(
        [sys.executable, "-m", "trading_agent_skills.cli.journal",
         "write", "--account-id", "12345678"],
        input=json.dumps(payload), text=True, capture_output=True,
    )
    assert res.returncode == 0, res.stderr
    expected = tmp_path / ".trading-agent-skills" / "accounts" / "12345678" / "journal.jsonl"
    assert expected.is_file()
```

- [ ] **Step 7: Run all journal tests**

```
./.venv/Scripts/python.exe -m pytest tests/test_journal_io.py tests/test_cli_journal.py -v
```
Expected: all PASSED

- [ ] **Step 8: Commit**

```
rtk git add src/trading_agent_skills/journal_io.py src/trading_agent_skills/cli/journal.py tests/test_journal_io.py tests/test_cli_journal.py
rtk git commit -m "feat(journal): per-account-id path resolution; --account-id CLI flag"
```

---

### Task 10: `daily_state` accepts `account_id`

**Files:**
- Modify: `src/trading_agent_skills/daily_state.py`
- Modify: `tests/test_daily_state.py`

- [ ] **Step 1: Inspect current daily_state**

Read `src/trading_agent_skills/daily_state.py` to find where the file path is currently set (likely a module-level constant `DEFAULT_DAILY_STATE_PATH` or equivalent).

- [ ] **Step 2: Add failing test**

```python
# Append to tests/test_daily_state.py
from trading_agent_skills.daily_state import default_daily_state_path


def test_default_daily_state_path_with_account_id(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    p = default_daily_state_path(account_id="12345678")
    assert p == tmp_path / ".trading-agent-skills" / "accounts" / "12345678" / "daily_state.json"


def test_default_daily_state_path_legacy(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    p = default_daily_state_path(account_id=None)
    assert p == tmp_path / ".trading-agent-skills" / "daily_state.json"
```

- [ ] **Step 3: Implement**

Append to `src/trading_agent_skills/daily_state.py`:

```python
def default_daily_state_path(account_id: Optional[str] = None) -> Path:
    base = Path.home() / ".trading-agent-skills"
    if account_id:
        from trading_agent_skills.account_paths import resolve_account_paths

        return resolve_account_paths(account_id=account_id).daily_state
    return base / "daily_state.json"
```

(Add `from typing import Optional` and `from pathlib import Path` if not already present.)

- [ ] **Step 4: Run tests**

```
./.venv/Scripts/python.exe -m pytest tests/test_daily_state.py -v
```
Expected: all PASSED

- [ ] **Step 5: Commit**

```
rtk git add src/trading_agent_skills/daily_state.py tests/test_daily_state.py
rtk git commit -m "feat(daily-state): per-account-id path resolution"
```

---

## Phase 4 — trading-heartbeat skill (markdown)

### Task 11: Write `trading-heartbeat/SKILL.md`

**Files:**
- Create: `.claude/skills/trading-heartbeat/SKILL.md`

This is markdown only — no Python, no tests. Verification is end-to-end via smoke test.

- [ ] **Step 1: Create the skill file**

```markdown
<!-- .claude/skills/trading-heartbeat/SKILL.md -->
---
name: trading-heartbeat
description: |
  Use to run one autonomous trading cycle (a "tick") on a configured demo MT5
  account. Triggered by the harness on a recurring schedule matching the
  charter's heartbeat (15m / 1h / 4h via /loop, OpenClaw cron, or Hermes
  heartbeat). Each tick reads the operating charter, checks kill conditions
  (guardian HALT, market closed, broker unreachable), manages open positions
  (close/modify based on structural re-evaluation), and scans for new entries
  through pre-trade-checklist + position-sizer. Every action — and every
  evaluated-but-skipped candidate — is logged to the decision log with
  reasoning. Trade execution uses mt5-mcp (place_order / close_position /
  modify_order). Read-write to the demo account; never operates on live mode
  unless charter.mode is explicitly "live".
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
```

- [ ] **Step 2: Verify file lands in skill discovery**

```
ls .claude/skills/trading-heartbeat/
```
Expected: `SKILL.md` listed.

- [ ] **Step 3: Commit**

```
rtk git add .claude/skills/trading-heartbeat/SKILL.md
rtk git commit -m "feat(skill): trading-heartbeat orchestrator markdown"
```

---

## Phase 5 — strategy-review skill

### Task 12: `strategy_review` performance summary aggregator

**Files:**
- Create: `src/trading_agent_skills/strategy_review.py`
- Test: `tests/test_strategy_review.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_strategy_review.py
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from trading_agent_skills.account_paths import resolve_account_paths
from trading_agent_skills.journal_io import write_open
from trading_agent_skills.strategy_review import compute_performance_summary


def _seed_journal(path: Path, n_wins: int, n_losses: int) -> None:
    base = datetime(2026, 4, 25, 8, 0, 0, tzinfo=timezone.utc)
    for i in range(n_wins):
        write_open(
            path, symbol="XAUUSD.z", side="buy", volume="0.1",
            entry_price="2380.00", exit_price="2390.00",
            entry_time=base + timedelta(days=i),
            exit_time=base + timedelta(days=i, hours=4),
            original_stop_distance_points=50,
            original_risk_amount="100.00", realized_pnl="100.00",
            swap_accrued="0.00", commission="0.00",
            setup_type="price_action:pin_bar", rationale="test",
            risk_classification_at_close="AT_RISK",
        )
    for i in range(n_losses):
        write_open(
            path, symbol="EURUSD.z", side="buy", volume="0.1",
            entry_price="1.0800", exit_price="1.0750",
            entry_time=base + timedelta(days=n_wins + i),
            exit_time=base + timedelta(days=n_wins + i, hours=4),
            original_stop_distance_points=50,
            original_risk_amount="100.00", realized_pnl="-100.00",
            swap_accrued="0.00", commission="0.00",
            setup_type="price_action:fvg_fill", rationale="test",
            risk_classification_at_close="AT_RISK",
        )


def test_perf_summary_counts(tmp_path: Path) -> None:
    paths = resolve_account_paths(account_id="12345678", base=tmp_path)
    paths.ensure_dirs()
    _seed_journal(paths.journal, n_wins=3, n_losses=2)
    summary = compute_performance_summary(
        paths,
        since=datetime(2026, 4, 1, tzinfo=timezone.utc),
        until=datetime(2026, 5, 10, tzinfo=timezone.utc),
    )
    assert summary["trades_closed"] == 5
    assert summary["wins"] == 3
    assert summary["losses"] == 2
    assert summary["win_rate"] == pytest.approx(60.0)
    assert Decimal(summary["realized_pnl"]) == Decimal("100.00")  # 3*100 - 2*100


def test_perf_summary_excludes_outside_window(tmp_path: Path) -> None:
    paths = resolve_account_paths(account_id="12345678", base=tmp_path)
    paths.ensure_dirs()
    _seed_journal(paths.journal, n_wins=1, n_losses=0)
    summary = compute_performance_summary(
        paths,
        since=datetime(2026, 5, 1, tzinfo=timezone.utc),
        until=datetime(2026, 5, 10, tzinfo=timezone.utc),
    )
    assert summary["trades_closed"] == 0


def test_perf_summary_handles_empty_journal(tmp_path: Path) -> None:
    paths = resolve_account_paths(account_id="12345678", base=tmp_path)
    paths.ensure_dirs()
    paths.journal.touch()
    summary = compute_performance_summary(
        paths,
        since=datetime(2026, 4, 1, tzinfo=timezone.utc),
        until=datetime(2026, 5, 10, tzinfo=timezone.utc),
    )
    assert summary["trades_closed"] == 0
    assert summary["win_rate"] is None
```

- [ ] **Step 2: Run, verify failure**

```
./.venv/Scripts/python.exe -m pytest tests/test_strategy_review.py -v
```
Expected: 3 FAILED (module/function not defined).

- [ ] **Step 3: Implement**

```python
# src/trading_agent_skills/strategy_review.py
"""Weekly strategy review — aggregates journal + decision-log + charter, emits
a markdown proposal that the user approves before any charter change is written.

This module ONLY produces proposals. It NEVER mutates the charter — that is the
caller's job after explicit user approval.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from trading_agent_skills.account_paths import AccountPaths
from trading_agent_skills.journal_io import read_resolved


def compute_performance_summary(
    paths: AccountPaths,
    *,
    since: datetime,
    until: datetime,
) -> dict[str, Any]:
    """Aggregate journal entries within [since, until) into a summary dict."""
    if not paths.journal.is_file():
        return _empty_summary()

    closed = [
        e for e in read_resolved(paths.journal)
        if _within(e.get("entry_time"), since, until) or _within(e.get("exit_time"), since, until)
    ]
    if not closed:
        return _empty_summary()

    wins = sum(1 for e in closed if Decimal(e["realized_pnl"]) > 0)
    losses = sum(1 for e in closed if Decimal(e["realized_pnl"]) < 0)
    pnl = sum((Decimal(e["realized_pnl"]) for e in closed), Decimal("0"))

    return {
        "trades_closed": len(closed),
        "wins": wins,
        "losses": losses,
        "win_rate": float(wins) * 100.0 / len(closed) if closed else None,
        "realized_pnl": format(pnl, "f"),
    }


def _empty_summary() -> dict[str, Any]:
    return {
        "trades_closed": 0, "wins": 0, "losses": 0,
        "win_rate": None, "realized_pnl": "0",
    }


def _within(ts: Optional[str], since: datetime, until: datetime) -> bool:
    if not ts:
        return False
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return since <= dt < until
```

- [ ] **Step 4: Run tests**

```
./.venv/Scripts/python.exe -m pytest tests/test_strategy_review.py -v
```
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```
rtk git add src/trading_agent_skills/strategy_review.py tests/test_strategy_review.py
rtk git commit -m "feat(strategy-review): performance summary aggregator"
```

---

### Task 13: setup-type win-rate breakdown

**Files:**
- Modify: `src/trading_agent_skills/strategy_review.py`
- Modify: `tests/test_strategy_review.py`

- [ ] **Step 1: Add failing test**

```python
# Append to tests/test_strategy_review.py
from trading_agent_skills.strategy_review import compute_setup_breakdown


def test_setup_breakdown_per_label(tmp_path: Path) -> None:
    paths = resolve_account_paths(account_id="12345678", base=tmp_path)
    paths.ensure_dirs()
    _seed_journal(paths.journal, n_wins=3, n_losses=2)
    bd = compute_setup_breakdown(
        paths,
        since=datetime(2026, 4, 1, tzinfo=timezone.utc),
        until=datetime(2026, 5, 10, tzinfo=timezone.utc),
    )
    pin = next(b for b in bd if b["setup_type"] == "price_action:pin_bar")
    fvg = next(b for b in bd if b["setup_type"] == "price_action:fvg_fill")
    assert pin["wins"] == 3
    assert pin["losses"] == 0
    assert fvg["wins"] == 0
    assert fvg["losses"] == 2
```

- [ ] **Step 2: Run, verify failure**

```
./.venv/Scripts/python.exe -m pytest tests/test_strategy_review.py -k setup_breakdown -v
```
Expected: FAIL.

- [ ] **Step 3: Implement**

Append to `src/trading_agent_skills/strategy_review.py`:

```python
def compute_setup_breakdown(
    paths: AccountPaths,
    *,
    since: datetime,
    until: datetime,
) -> list[dict[str, Any]]:
    """Group closed trades by setup_type, return list of {setup_type, wins, losses, pnl}."""
    if not paths.journal.is_file():
        return []
    closed = [
        e for e in read_resolved(paths.journal)
        if _within(e.get("entry_time"), since, until) or _within(e.get("exit_time"), since, until)
    ]
    by_setup: dict[str, dict[str, Any]] = {}
    for e in closed:
        st = e.get("setup_type", "unknown")
        bucket = by_setup.setdefault(st, {"setup_type": st, "wins": 0, "losses": 0, "pnl": Decimal("0")})
        pnl = Decimal(e["realized_pnl"])
        bucket["pnl"] += pnl
        if pnl > 0:
            bucket["wins"] += 1
        elif pnl < 0:
            bucket["losses"] += 1
    return [
        {**b, "pnl": format(b["pnl"], "f")} for b in by_setup.values()
    ]
```

- [ ] **Step 4: Run tests**

```
./.venv/Scripts/python.exe -m pytest tests/test_strategy_review.py -v
```
Expected: 4 PASSED

- [ ] **Step 5: Commit**

```
rtk git add src/trading_agent_skills/strategy_review.py tests/test_strategy_review.py
rtk git commit -m "feat(strategy-review): setup-type win/loss/pnl breakdown"
```

---

### Task 14: decision-log skip-reason analysis

**Files:**
- Modify: `src/trading_agent_skills/strategy_review.py`
- Modify: `tests/test_strategy_review.py`

- [ ] **Step 1: Add failing test**

```python
# Append to tests/test_strategy_review.py
from collections import Counter

from trading_agent_skills.decision_log import write_intent
from trading_agent_skills.strategy_review import compute_decision_summary


def test_decision_summary_groups_skip_reasons(tmp_path: Path) -> None:
    paths = resolve_account_paths(account_id="12345678", base=tmp_path)
    paths.ensure_dirs()
    skip_reasons = ["spread_too_wide", "spread_too_wide", "guardian_caution"]
    for i, reason in enumerate(skip_reasons):
        write_intent(
            paths.decisions, kind="skip", symbol="X", ticket=None,
            setup_type="price_action:pin_bar", reasoning=reason,
            skills_used=[], guardian_status="CLEAR", checklist_verdict="BLOCK",
            execution=None, charter_version=1,
            tick_id=f"2026-04-{29 + i}T22:00:00Z",
        )
    summary = compute_decision_summary(
        paths,
        since=datetime(2026, 4, 1, tzinfo=timezone.utc),
        until=datetime(2026, 5, 10, tzinfo=timezone.utc),
    )
    assert summary["total_decisions"] == 3
    assert summary["skips"] == 3
    assert summary["entries"] == 0
    assert summary["top_skip_reasons"][0] == ("spread_too_wide", 2)
```

- [ ] **Step 2: Run, verify failure**

Expected: FAIL.

- [ ] **Step 3: Implement**

Append to `src/trading_agent_skills/strategy_review.py`:

```python
from trading_agent_skills.decision_log import filter_decisions


def compute_decision_summary(
    paths: AccountPaths,
    *,
    since: datetime,
    until: datetime,
) -> dict[str, Any]:
    """Aggregate decision-log activity in window."""
    if not paths.decisions.is_file():
        return {
            "total_decisions": 0, "skips": 0, "entries": 0,
            "closes": 0, "modifies": 0, "top_skip_reasons": [],
        }
    recs = [
        r for r in filter_decisions(paths.decisions, since=since)
        if _tick_within(r.get("tick_id"), since, until)
    ]
    skip_reasons = [r["reasoning"] for r in recs if r["kind"] == "skip"]
    counter: dict[str, int] = {}
    for reason in skip_reasons:
        counter[reason] = counter.get(reason, 0) + 1
    top = sorted(counter.items(), key=lambda kv: -kv[1])[:5]
    return {
        "total_decisions": len(recs),
        "skips": sum(1 for r in recs if r["kind"] == "skip"),
        "entries": sum(1 for r in recs if r["kind"] == "open"),
        "closes": sum(1 for r in recs if r["kind"] == "close"),
        "modifies": sum(1 for r in recs if r["kind"] == "modify"),
        "top_skip_reasons": top,
    }


def _tick_within(tick: Optional[str], since: datetime, until: datetime) -> bool:
    if not tick:
        return False
    dt = datetime.fromisoformat(tick.replace("Z", "+00:00"))
    return since <= dt < until
```

- [ ] **Step 4: Run tests**

```
./.venv/Scripts/python.exe -m pytest tests/test_strategy_review.py -v
```
Expected: 5 PASSED

- [ ] **Step 5: Commit**

```
rtk git add src/trading_agent_skills/strategy_review.py tests/test_strategy_review.py
rtk git commit -m "feat(strategy-review): decision-log activity and skip-reason summary"
```

---

### Task 15: proposal generator with locked-field protection

**Files:**
- Modify: `src/trading_agent_skills/strategy_review.py`
- Modify: `tests/test_strategy_review.py`

The proposal generator is INTENTIONALLY conservative — it produces structural recommendations (e.g., "consider tightening per_trade_risk_pct") but the actual deltas are filled in by the LLM in the SKILL.md flow. The Python guarantees structural integrity (locked-field protection, valid YAML output); the LLM provides the judgement.

- [ ] **Step 1: Add failing test**

```python
# Append to tests/test_strategy_review.py
from trading_agent_skills.charter_io import parse_charter
from trading_agent_skills.strategy_review import (
    PROPOSABLE_FIELDS,
    apply_proposal,
    build_proposal_skeleton,
    validate_proposal_diff,
)


_VALID_CHARTER_TEXT = """\
mode: demo
account_id: 12345678
heartbeat: 1h
hard_caps:
  per_trade_risk_pct: 1.0
  daily_loss_pct: 5.0
  max_concurrent_positions: 3
charter_version: 1
created_at: 2026-04-30T14:00:00+10:00
created_account_balance: 10000.00
trading_style: day
sessions_allowed: []
instruments: []
allowed_setups: []
notes: ""
"""


def test_proposable_fields_excludes_locked() -> None:
    assert "mode" not in PROPOSABLE_FIELDS
    assert "account_id" not in PROPOSABLE_FIELDS
    assert "created_at" not in PROPOSABLE_FIELDS
    assert "created_account_balance" not in PROPOSABLE_FIELDS
    assert "charter_version" not in PROPOSABLE_FIELDS
    assert "per_trade_risk_pct" in PROPOSABLE_FIELDS
    assert "instruments" in PROPOSABLE_FIELDS
    assert "allowed_setups" in PROPOSABLE_FIELDS


def test_validate_proposal_rejects_locked_field_change() -> None:
    bad = {"mode": "live"}
    with pytest.raises(ValueError, match="locked"):
        validate_proposal_diff(bad)


def test_validate_proposal_accepts_proposable_fields() -> None:
    ok = {"per_trade_risk_pct": 0.8, "allowed_setups": ["price_action:pin_bar"]}
    validate_proposal_diff(ok)  # no exception


def test_build_proposal_skeleton_emits_markdown(tmp_path: Path) -> None:
    paths = resolve_account_paths(account_id="12345678", base=tmp_path)
    paths.ensure_dirs()
    paths.charter.write_text(_VALID_CHARTER_TEXT, encoding="utf-8")
    paths.journal.touch()
    md = build_proposal_skeleton(
        paths,
        since=datetime(2026, 4, 25, tzinfo=timezone.utc),
        until=datetime(2026, 5, 2, tzinfo=timezone.utc),
    )
    assert "Strategy review" in md
    assert "Performance summary" in md
    assert "Decision-log analysis" in md
    assert "Charter diff proposal" in md
    assert "Reply with" in md


def test_apply_proposal_increments_version_and_archives(tmp_path: Path) -> None:
    paths = resolve_account_paths(account_id="12345678", base=tmp_path)
    paths.ensure_dirs()
    paths.charter.write_text(_VALID_CHARTER_TEXT, encoding="utf-8")
    new_charter = apply_proposal(
        paths,
        approved_changes={"per_trade_risk_pct": 0.8},
    )
    assert new_charter.charter_version == 2
    assert new_charter.hard_caps.per_trade_risk_pct == 0.8
    assert (paths.charter_versions / "v1.md").is_file()
    assert "per_trade_risk_pct: 0.8" in paths.charter.read_text(encoding="utf-8")


def test_apply_proposal_rejects_locked_field(tmp_path: Path) -> None:
    paths = resolve_account_paths(account_id="12345678", base=tmp_path)
    paths.ensure_dirs()
    paths.charter.write_text(_VALID_CHARTER_TEXT, encoding="utf-8")
    with pytest.raises(ValueError, match="locked"):
        apply_proposal(paths, approved_changes={"mode": "live"})
```

- [ ] **Step 2: Run, verify failure**

Expected: 6 FAILED.

- [ ] **Step 3: Implement**

Append to `src/trading_agent_skills/strategy_review.py`:

```python
from trading_agent_skills.charter_io import (
    LOCKED_FIELDS,
    Charter,
    HardCaps,
    parse_charter,
    write_charter_with_archive,
)


# All charter fields the user/agent could conceivably tune. Locked fields
# are excluded.
PROPOSABLE_FIELDS = frozenset({
    # hard_caps members (flattened for proposal-diff convenience)
    "per_trade_risk_pct", "daily_loss_pct", "max_concurrent_positions",
    # soft fields
    "trading_style", "heartbeat",
    "sessions_allowed", "instruments", "allowed_setups", "notes",
})


def validate_proposal_diff(diff: dict[str, Any]) -> None:
    """Raise ValueError if the diff touches any locked field or unknown field."""
    for key in diff.keys():
        if key in LOCKED_FIELDS:
            raise ValueError(f"field {key!r} is locked and cannot be proposed")
        if key not in PROPOSABLE_FIELDS:
            raise ValueError(f"field {key!r} is not a known proposable field")


def build_proposal_skeleton(
    paths: AccountPaths,
    *,
    since: datetime,
    until: datetime,
) -> str:
    """Emit a markdown skeleton the LLM fills in with judgements.

    The Python provides aggregated facts; the LLM provides analysis and
    diff proposals. The skeleton has placeholder sections for the diff
    so the LLM has clear slots to fill.
    """
    perf = compute_performance_summary(paths, since=since, until=until)
    by_setup = compute_setup_breakdown(paths, since=since, until=until)
    decisions = compute_decision_summary(paths, since=since, until=until)

    setup_lines = "\n".join(
        f"- {b['setup_type']}: {b['wins']}W / {b['losses']}L, P&L {b['pnl']}"
        for b in by_setup
    ) or "- (no closed trades in window)"

    skip_lines = "\n".join(
        f"- {reason}: {count}" for reason, count in decisions["top_skip_reasons"]
    ) or "- (no skips logged)"

    return f"""# Strategy review — {until.date().isoformat()}

## Performance summary ({since.date().isoformat()} → {until.date().isoformat()})

- Trades closed: {perf['trades_closed']} ({perf['wins']}W / {perf['losses']}L)
- Win rate: {perf['win_rate']}
- Realized P&L: {perf['realized_pnl']}

## Setup-type breakdown

{setup_lines}

## Decision-log analysis

- Total decisions: {decisions['total_decisions']}
- Entries: {decisions['entries']}, Closes: {decisions['closes']}, Modifies: {decisions['modifies']}, Skips: {decisions['skips']}
- Top skip reasons:

{skip_lines}

## Charter diff proposal (requires approval)

<!-- LLM: fill this section with concrete proposed changes based on the
     stats above. Use a YAML diff fence. Only fields in PROPOSABLE_FIELDS
     may appear. Locked fields are forbidden. -->

```diff
# (LLM-filled)
```

### Reasoning

<!-- LLM: explain the reasoning for each proposed change in 1-2 sentences. -->

## Reply with

- "approve all" — apply every change above
- "approve <fields>" — apply only listed (e.g., "approve per_trade_risk_pct, allowed_setups")
- "reject" — no changes; proposal archived as-is
- "discuss <topic>" — ask clarifying question
"""


def apply_proposal(
    paths: AccountPaths,
    *,
    approved_changes: dict[str, Any],
) -> Charter:
    """Apply approved field changes to the charter, bump version, archive prior."""
    validate_proposal_diff(approved_changes)
    current = parse_charter(paths.charter.read_text(encoding="utf-8"))

    # Build the new charter from the current, overlaying approved changes.
    new_caps = HardCaps(
        per_trade_risk_pct=approved_changes.get("per_trade_risk_pct", current.hard_caps.per_trade_risk_pct),
        daily_loss_pct=approved_changes.get("daily_loss_pct", current.hard_caps.daily_loss_pct),
        max_concurrent_positions=approved_changes.get(
            "max_concurrent_positions", current.hard_caps.max_concurrent_positions
        ),
    )
    new_charter = Charter(
        mode=current.mode,
        account_id=current.account_id,
        heartbeat=approved_changes.get("heartbeat", current.heartbeat),
        hard_caps=new_caps,
        charter_version=current.charter_version + 1,
        created_at=current.created_at,
        created_account_balance=current.created_account_balance,
        trading_style=approved_changes.get("trading_style", current.trading_style),
        sessions_allowed=approved_changes.get("sessions_allowed", current.sessions_allowed),
        instruments=approved_changes.get("instruments", current.instruments),
        allowed_setups=approved_changes.get("allowed_setups", current.allowed_setups),
        notes=approved_changes.get("notes", current.notes),
    )
    write_charter_with_archive(paths, new_charter)
    return new_charter
```

- [ ] **Step 4: Run tests**

```
./.venv/Scripts/python.exe -m pytest tests/test_strategy_review.py -v
```
Expected: 11 PASSED

- [ ] **Step 5: Commit**

```
rtk git add src/trading_agent_skills/strategy_review.py tests/test_strategy_review.py
rtk git commit -m "feat(strategy-review): proposal skeleton, diff validation, charter apply"
```

---

### Task 16: strategy-review CLI

**Files:**
- Create: `src/trading_agent_skills/cli/strategy_review.py`
- Create: `tests/test_cli_strategy_review.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_cli_strategy_review.py
import json
import subprocess
import sys
from pathlib import Path

import pytest

from trading_agent_skills.account_paths import resolve_account_paths


_VALID_CHARTER_TEXT = """\
mode: demo
account_id: 12345678
heartbeat: 1h
hard_caps:
  per_trade_risk_pct: 1.0
  daily_loss_pct: 5.0
  max_concurrent_positions: 3
charter_version: 1
created_at: 2026-04-30T14:00:00+10:00
created_account_balance: 10000.00
trading_style: day
sessions_allowed: []
instruments: []
allowed_setups: []
notes: ""
"""


def test_cli_emits_proposal(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    paths = resolve_account_paths(account_id="12345678", base=tmp_path / ".trading-agent-skills")
    paths.ensure_dirs()
    paths.charter.write_text(_VALID_CHARTER_TEXT, encoding="utf-8")
    paths.journal.touch()

    res = subprocess.run(
        [sys.executable, "-m", "trading_agent_skills.cli.strategy_review",
         "propose", "--account-id", "12345678",
         "--since", "2026-04-25T00:00:00Z",
         "--until", "2026-05-02T00:00:00Z"],
        text=True, capture_output=True,
    )
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["status"] == "ok"
    proposal_path = Path(out["proposal_path"])
    assert proposal_path.is_file()
    assert "Strategy review" in proposal_path.read_text(encoding="utf-8")


def test_cli_apply_changes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    paths = resolve_account_paths(account_id="12345678", base=tmp_path / ".trading-agent-skills")
    paths.ensure_dirs()
    paths.charter.write_text(_VALID_CHARTER_TEXT, encoding="utf-8")

    res = subprocess.run(
        [sys.executable, "-m", "trading_agent_skills.cli.strategy_review",
         "apply", "--account-id", "12345678"],
        input=json.dumps({"per_trade_risk_pct": 0.8}),
        text=True, capture_output=True,
    )
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["status"] == "ok"
    assert out["new_version"] == 2
    assert "per_trade_risk_pct: 0.8" in paths.charter.read_text(encoding="utf-8")


def test_cli_apply_rejects_locked_field(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    paths = resolve_account_paths(account_id="12345678", base=tmp_path / ".trading-agent-skills")
    paths.ensure_dirs()
    paths.charter.write_text(_VALID_CHARTER_TEXT, encoding="utf-8")

    res = subprocess.run(
        [sys.executable, "-m", "trading_agent_skills.cli.strategy_review",
         "apply", "--account-id", "12345678"],
        input=json.dumps({"mode": "live"}),
        text=True, capture_output=True,
    )
    assert res.returncode != 0
    assert "locked" in (res.stderr + res.stdout)
```

- [ ] **Step 2: Run, verify failure**

```
./.venv/Scripts/python.exe -m pytest tests/test_cli_strategy_review.py -v
```
Expected: 3 FAILED (module not found).

- [ ] **Step 3: Implement**

```python
# src/trading_agent_skills/cli/strategy_review.py
"""CLI for the strategy-review skill — propose / apply.

Usage:
  trading-agent-skills-strategy-review propose --account-id <ID> --since <ISO> --until <ISO>
  echo '{"per_trade_risk_pct": 0.8}' | trading-agent-skills-strategy-review apply --account-id <ID>
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from trading_agent_skills.account_paths import resolve_account_paths
from trading_agent_skills.strategy_review import (
    apply_proposal,
    build_proposal_skeleton,
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="trading-agent-skills-strategy-review")
    sub = p.add_subparsers(dest="action", required=True)

    propose = sub.add_parser("propose", help="Generate a proposal skeleton.")
    propose.add_argument("--account-id", required=True)
    propose.add_argument("--since", required=True, help="ISO 8601, UTC")
    propose.add_argument("--until", required=True, help="ISO 8601, UTC")
    propose.set_defaults(func=_cmd_propose)

    apply = sub.add_parser("apply", help="Apply approved diff (JSON on stdin).")
    apply.add_argument("--account-id", required=True)
    apply.set_defaults(func=_cmd_apply)

    return p


def _cmd_propose(args: argparse.Namespace) -> int:
    paths = resolve_account_paths(account_id=args.account_id)
    paths.ensure_dirs()
    since = datetime.fromisoformat(args.since.replace("Z", "+00:00"))
    until = datetime.fromisoformat(args.until.replace("Z", "+00:00"))
    md = build_proposal_skeleton(paths, since=since, until=until)
    proposal_path = paths.proposals / f"{until.date().isoformat()}.md"
    proposal_path.write_text(md, encoding="utf-8")
    print(json.dumps({
        "status": "ok",
        "proposal_path": str(proposal_path),
    }))
    return 0


def _cmd_apply(args: argparse.Namespace) -> int:
    paths = resolve_account_paths(account_id=args.account_id)
    paths.ensure_dirs()
    diff = json.load(sys.stdin)
    try:
        new_charter = apply_proposal(paths, approved_changes=diff)
    except ValueError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps({
        "status": "ok",
        "new_version": new_charter.charter_version,
    }))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Add entry point in pyproject.toml**

Edit `pyproject.toml`, append under `[project.scripts]`:

```toml
trading-agent-skills-strategy-review = "trading_agent_skills.cli.strategy_review:main"
```

Reinstall the package:

```
./.venv/Scripts/python.exe -m pip install -e .
```

- [ ] **Step 5: Run tests**

```
./.venv/Scripts/python.exe -m pytest tests/test_cli_strategy_review.py -v
```
Expected: 3 PASSED

- [ ] **Step 6: Commit**

```
rtk git add src/trading_agent_skills/cli/strategy_review.py tests/test_cli_strategy_review.py pyproject.toml
rtk git commit -m "feat(strategy-review-cli): propose and apply subcommands + entry point"
```

---

### Task 17: Write `strategy-review/SKILL.md`

**Files:**
- Create: `.claude/skills/strategy-review/SKILL.md`

- [ ] **Step 1: Create the file**

```markdown
<!-- .claude/skills/strategy-review/SKILL.md -->
---
name: strategy-review
description: |
  Use when the user asks for a weekly strategy review, performance analysis,
  or wants to refine the autonomous-trading charter based on recent results.
  Triggers on phrases like "weekly review", "strategy review", "how did this
  week go for the agent", "should we tweak the charter". Reads journal +
  decision-log + spread baselines for the active account, builds a markdown
  proposal with structural recommendations, asks the user which changes to
  apply, then bumps the charter version. Never auto-applies. Cannot propose
  changes to mode (demo↔live) or account_id — those are user-initiated only.
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
```

- [ ] **Step 2: Verify file is in place**

```
ls .claude/skills/strategy-review/
```
Expected: `SKILL.md`

- [ ] **Step 3: Commit**

```
rtk git add .claude/skills/strategy-review/SKILL.md
rtk git commit -m "feat(skill): strategy-review weekly retrospective markdown"
```

---

## Phase 6 — Install and docs

### Task 18: AGENTS.md charter Q&A section

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Read current AGENTS.md to find insertion point**

```
./.venv/Scripts/python.exe -c "import pathlib; print(pathlib.Path('AGENTS.md').read_text(encoding='utf-8')[:500])"
```

- [ ] **Step 2: Add charter Q&A section**

Append to `AGENTS.md` (at the bottom, before any troubleshooting section):

```markdown
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
```

- [ ] **Step 3: Commit**

```
rtk git add AGENTS.md
rtk git commit -m "docs(agents): autonomous trading install Q&A"
```

---

### Task 19: AGENTS.md demo→live runbook

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Append the runbook**

Append to `AGENTS.md`:

```markdown
## Demo→live runbook

This is fired ONLY when the user explicitly says "switch <account_id> to
live" (or equivalent). Never propose this — wait for the user to ask.

### 1. Confirm prerequisites

- A live MT5 account is configured in the user's terminal.
- The demo account has at least 4 weekly strategy-review proposals in
  `~/.trading-agent-skills/accounts/<account_id>/proposals/`. If not, warn
  the user and ask if they're sure they want to skip the demo soak period.

### 2. Show full context

Read and present:

- The current `charter.md` in full.
- The last 4 weekly proposal files (summary lines from each).
- All-time performance from `trading-agent-skills-journal stats`.
- Current account balance via `get_account_info`.

### 3. Single confirmation

Ask exactly: "Confirm switching <account_id> to live trading? Reply 'yes'
to proceed, anything else to cancel."

If reply is anything other than `yes`, abort and confirm to the user that
no change was made.

### 4. Apply the flip

If `yes`:

- Archive current charter to `charter_versions/v<N>.md`.
- Write new charter with `mode: live` and bumped `charter_version`.
- Append a `mode_change` decision record to `decisions.jsonl` with
  `reasoning="user-initiated demo→live: <user phrase>"`.

```bash
trading-agent-skills-journal decision write --decisions-path ~/.trading-agent-skills/accounts/<id>/decisions.jsonl <<'EOF'
{
  "kind": "mode_change",
  "symbol": "*",
  "ticket": null,
  "setup_type": "system:mode_flip",
  "reasoning": "user-initiated demo→live: <user phrase>",
  "skills_used": [],
  "guardian_status": "CLEAR",
  "checklist_verdict": null,
  "execution": null,
  "charter_version": <new_version>,
  "tick_id": "<current ISO UTC>"
}
EOF
```

### 5. Confirm to user

Print: "Charter <id> is now in `live` mode. Heartbeat will operate on the
live account starting next tick. To revert, say 'switch <id> back to demo'."

### Reverse flow (live→demo)

Same workflow, symmetric. No additional ceremony beyond the single confirm.
```

- [ ] **Step 2: Commit**

```
rtk git add AGENTS.md
rtk git commit -m "docs(agents): demo→live runbook with single-confirm gate"
```

---

### Task 20: FUTURE.md for backtest TODO + CLAUDE.md status update

**Files:**
- Create: `FUTURE.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Create FUTURE.md**

```markdown
<!-- FUTURE.md -->
# Future TODOs (out of scope for current shipped work)

## mt5-mcp backtest support

**Why:** Currently the autonomous-trading-loop has no simulation path —
agent learns entirely on live demo data. Adding MT5 strategy-tester
support to mt5-mcp would unlock:

- Validating strategy-review proposals against historical data before
  applying them to charter.
- Backtesting newly-allowed setup labels before they go live.
- Onboarding new instruments without spending a week on demo ticks.

**Where:** Upstream change in `https://github.com/vincentwongso/mt5-mcp`.
MT5 exposes the strategy tester via the IDE; programmatic access requires
the `MqlTester` API or the recently-added Python `MetaTrader5.copy_rates_*`
combined with manual entry/exit simulation.

**Priority:** Low. Safe to operate without it on a demo account.

## Correlation matrix for exposure-overlap

The pre-trade-checklist exposure-overlap heuristic is shared-currency-only
(EURUSD long + EURGBP short → flagged). Real correlation between e.g.
USOIL/UKOIL/NAS100 (broad-risk-on) is not captured. A small CSV-driven
correlation matrix would tighten the heuristic.

## Sentiment classification on news articles

session-news-brief currently uses keyword-driven impact only. A small
classifier (or an LLM call) on article body could meaningfully improve
swing-candidate quality.

## Multi-account simultaneous trading

Out of scope by design. v1 supports one charter per session; switching
accounts is a clean-slate operation. Multi-account would require a
concurrency model (which heartbeat fires when two are due simultaneously).

## Auto-pause on extended drawdown

Charter could grow `pause_after_consecutive_losses: N` in v2. Avoiding
in v1 to keep the install Q&A short.
```

- [ ] **Step 2: Update CLAUDE.md status section**

Find the `## Status` section in `CLAUDE.md` and update:

```diff
-## Status (last updated 2026-04-29)
-
-All five skill bundles shipped on `main`:
+## Status (last updated 2026-04-30)
+
+All five advisory skill bundles shipped on `main`, plus the autonomous
+trading loop:
 - ✅ `position-sizer` — lot sizing + margin cross-check + swap-aware output
 - ✅ `trade-journal` — append-only JSONL with R-multiple, swap-only P&L, swing-trade lens
 - ✅ `daily-risk-guardian` + `pre-trade-checklist` (paired) — NY-close session reset, LLM-judged AT_RISK predicate, Calix proximity, EWMA spread baseline
 - ✅ `session-news-brief` — 5-tier watchlist resolver, 3-API news fan-out + dedup, ATR/RSI swing candidates, Calix calendar overlay
 - ✅ `price-action` — hybrid classical + ICT structural reader, 9 detectors, structural quality scoring, hands off to checklist + sizer
+- ✅ `trading-heartbeat` — autonomous tick orchestrator (composes the 6 above)
+- ✅ `strategy-review` — weekly retrospective + user-gated charter tuning
+- ✅ `trade-journal decision` subcommand — intent/outcome reasoning trail
+- ✅ Per-account state namespacing under `~/.trading-agent-skills/accounts/<id>/`
+- ✅ Operating charter (hard caps + soft fields) with version archival
```

Also update the `Layout` section to add the new files:

```diff
   trade-journal/SKILL.md + scripts/journal.py
   ...
+  trading-heartbeat/SKILL.md
+  strategy-review/SKILL.md
 src/trading_agent_skills/
+  account_paths.py     # per-account namespace resolver
+  charter_io.py        # operating charter parse/validate/write/archive
+  decision_log.py      # decisions.jsonl intent/outcome with reconciliation
+  strategy_review.py   # weekly performance + charter proposal generator
+  cli/strategy_review.py
   ...
 ~/.trading-agent-skills/         # runtime files (not committed):
+  accounts/<account_id>/         # NEW: per-account namespace
+    charter.md                   # operating charter
+    charter_versions/v<N>.md     # archived prior charters
+    decisions.jsonl              # intent + outcome records
+    proposals/<date>.md          # weekly review proposals
+    journal.jsonl                # per-account journal (vs. legacy root)
+    daily_state.json             # per-account session bookkeeping
   journal.jsonl                  # legacy root journal (manual mode)
   ...
```

- [ ] **Step 3: Run the full test suite to confirm no regressions**

```
./.venv/Scripts/python.exe -m pytest tests/ -q
```
Expected: all PASSED (existing 443 cases + new ~50 cases).

- [ ] **Step 4: Commit**

```
rtk git add FUTURE.md CLAUDE.md
rtk git commit -m "docs: track autonomous-trading-loop in CLAUDE.md status, add FUTURE.md"
```

---

## Final verification

- [ ] **Run full test suite**

```
./.venv/Scripts/python.exe -m pytest tests/ -q
```
Expected: all PASSED.

- [ ] **Verify CLI entry points**

```
trading-agent-skills-journal decision read --help
trading-agent-skills-strategy-review propose --help
```
Expected: usage text printed for both.

- [ ] **Confirm skill discovery**

```
ls .claude/skills/
```
Expected: includes `trading-heartbeat/` and `strategy-review/`.

- [ ] **Manual smoke flow (optional, requires mt5-mcp connected)**

1. Run install Q&A by saying to the agent: "set up autonomous trading."
2. Verify `~/.trading-agent-skills/accounts/<id>/charter.md` exists.
3. Fire one heartbeat tick: `/loop 15m /trading-heartbeat` (or trigger once).
4. Inspect `decisions.jsonl` — should have at least one record (likely a skip).
5. Run `/strategy-review` after a few ticks — should produce a proposal file.

---

## Self-review checklist (run before handoff)

- [ ] Every spec section §1-17 has at least one task implementing it.
- [ ] No "TBD" / "TODO" / "implement later" placeholders in any task body.
- [ ] Function names referenced in later tasks (`apply_proposal`, `write_intent`, etc.) match the names defined in earlier tasks.
- [ ] Every test step shows the actual test code, not "write tests for the above."
- [ ] Every implementation step shows the actual code.
- [ ] Locked-field protection is tested in BOTH directions: `validate_proposal_diff` raises on locked, `apply_proposal` raises on locked.
- [ ] Per-account namespacing has tests for both branches (with and without `account_id`).
- [ ] All commits are conventional-commit style with no Co-Authored-By trailer.
