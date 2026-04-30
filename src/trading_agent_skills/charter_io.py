"""Operating-charter YAML parser + validator.

Charter shape is fixed; we hand-roll a small parser to avoid PyYAML.
Keep this strict — bad data here silently changes trading behavior.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from trading_agent_skills.account_paths import AccountPaths


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
_NESTED_RE = re.compile(r"^(?:[ ]{2,}|\t+)([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$")


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

    non_empty_string_fields = ("mode", "account_id", "heartbeat", "created_at", "trading_style")
    for key in non_empty_string_fields:
        if not top[key]:
            raise CharterError(f"{key} must be a non-empty string")

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

    try:
        per_trade = float(hc["per_trade_risk_pct"])
    except ValueError as exc:
        raise CharterError(f"per_trade_risk_pct: not a valid number — {exc}") from exc
    if not 0 < per_trade <= 5.0:
        raise CharterError(f"per_trade_risk_pct must be in (0, 5.0], got {per_trade}")
    try:
        daily_loss = float(hc["daily_loss_pct"])
    except ValueError as exc:
        raise CharterError(f"daily_loss_pct: not a valid number — {exc}") from exc
    if not 0 < daily_loss <= 20.0:
        raise CharterError(f"daily_loss_pct must be in (0, 20.0], got {daily_loss}")
    try:
        max_conc = int(hc["max_concurrent_positions"])
    except ValueError as exc:
        raise CharterError(f"max_concurrent_positions: not a valid integer — {exc}") from exc
    if not 1 <= max_conc <= 20:
        raise CharterError(
            f"max_concurrent_positions must be in [1, 20], got {max_conc}"
        )

    sessions = _parse_list(top.get("sessions_allowed", "[]"))
    for s in sessions:
        if s not in ALLOWED_SESSIONS:
            raise CharterError(f"sessions_allowed[] entry {s!r} not in {ALLOWED_SESSIONS}")

    try:
        charter_version = int(top["charter_version"])
    except ValueError as exc:
        raise CharterError(f"charter_version: not a valid integer — {exc}") from exc
    try:
        created_account_balance = float(top["created_account_balance"])
    except ValueError as exc:
        raise CharterError(f"created_account_balance: not a valid number — {exc}") from exc

    return Charter(
        mode=mode,
        account_id=top["account_id"],
        heartbeat=heartbeat,
        hard_caps=HardCaps(
            per_trade_risk_pct=per_trade,
            daily_loss_pct=daily_loss,
            max_concurrent_positions=max_conc,
        ),
        charter_version=charter_version,
        created_at=top["created_at"],
        created_account_balance=created_account_balance,
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


_FORBIDDEN_IN_NOTES = ('"', "\n")
_FORBIDDEN_IN_LIST_ITEM = ('"', ",", "\n")


def _validate_renderable(c: "Charter") -> None:
    for ch in _FORBIDDEN_IN_NOTES:
        if ch in c.notes:
            raise CharterError(
                f"notes contains forbidden character {ch!r} — strategy-review must sanitise before write"
            )
    for field_name, items in (
        ("sessions_allowed", c.sessions_allowed),
        ("instruments", c.instruments),
        ("allowed_setups", c.allowed_setups),
    ):
        for item in items:
            for ch in _FORBIDDEN_IN_LIST_ITEM:
                if ch in item:
                    raise CharterError(
                        f"{field_name}[] entry {item!r} contains forbidden character {ch!r}"
                    )


def render_charter(c: Charter) -> str:
    """Render a Charter back to the YAML-like text format parse_charter consumes."""
    _validate_renderable(c)
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
    """Write the charter to `path`, creating parent dirs if missing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_charter(c), encoding="utf-8")


def write_charter_with_archive(paths: AccountPaths, new: Charter) -> None:
    """Archive the current charter to charter_versions/v<N>.md, then overwrite.

    Refuses if new.charter_version is not strictly greater than the current
    on-disk version. Caller is responsible for bumping charter_version.

    Creates parent dirs (charter_versions, account root) if missing.
    """
    if paths.charter.is_file():
        old = parse_charter(paths.charter.read_text(encoding="utf-8"))
        if new.charter_version <= old.charter_version:
            raise CharterError(
                f"new charter must increment charter_version above {old.charter_version}, "
                f"got {new.charter_version}"
            )
        paths.charter_versions.mkdir(parents=True, exist_ok=True)
        archive_path = paths.charter_versions / f"v{old.charter_version}.md"
        archive_path.write_text(render_charter(old), encoding="utf-8")
    write_charter(paths.charter, new)
