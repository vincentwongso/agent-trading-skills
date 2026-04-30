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
