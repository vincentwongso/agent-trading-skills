"""NY-close session bookkeeping for ``daily-risk-guardian``.

"Today" is defined as the window since the most recent ``reset_time`` in
``reset_tz`` — for the user's defaults that's NY 4pm ET, which is 6am AEST
local. ``zoneinfo`` resolves DST transitions automatically, so spring-forward
and fall-back days don't drift the reset moment in wall-clock terms.

State persists to ``~/.trading-agent-skills/daily_state.json``:

    {
        "session_open_balance": "10000.00",
        "last_reset_utc": "2026-04-29T20:00:00+00:00"
    }

The first invocation of ``tick(...)`` after ``compute_last_reset`` advances
past the stored ``last_reset_utc`` snapshots the current equity as the new
session-open balance.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from trading_agent_skills.decimal_io import D


DEFAULT_STATE_PATH = Path.home() / ".trading-agent-skills" / "daily_state.json"


@dataclass(frozen=True)
class DailyState:
    session_open_balance: Decimal
    last_reset_utc: datetime  # tz-aware UTC


@dataclass(frozen=True)
class SessionInfo:
    state: DailyState
    next_reset_utc: datetime
    just_reset: bool  # True iff this tick crossed a reset boundary


def _parse_reset_time(reset_time: str) -> time:
    hh, mm = reset_time.split(":")
    return time(int(hh), int(mm))


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("naive datetime passed; expected tz-aware UTC")
    return dt.astimezone(timezone.utc)


def compute_last_reset(
    now_utc: datetime, *, reset_tz: str, reset_time: str
) -> datetime:
    """Return the most recent reset moment (in UTC) at or before ``now_utc``."""
    now_utc = _ensure_utc(now_utc)
    tz = ZoneInfo(reset_tz)
    now_local = now_utc.astimezone(tz)
    rt = _parse_reset_time(reset_time)

    today_reset_local = datetime.combine(now_local.date(), rt, tzinfo=tz)
    if now_local >= today_reset_local:
        last_local = today_reset_local
    else:
        yesterday: date = now_local.date() - timedelta(days=1)
        last_local = datetime.combine(yesterday, rt, tzinfo=tz)
    return last_local.astimezone(timezone.utc)


def compute_next_reset(
    now_utc: datetime, *, reset_tz: str, reset_time: str
) -> datetime:
    """Return the next reset moment (in UTC) strictly after ``now_utc``."""
    last = compute_last_reset(now_utc, reset_tz=reset_tz, reset_time=reset_time)
    tz = ZoneInfo(reset_tz)
    last_local = last.astimezone(tz)
    next_local = datetime.combine(
        last_local.date() + timedelta(days=1),
        last_local.timetz().replace(tzinfo=None),
        tzinfo=tz,
    )
    return next_local.astimezone(timezone.utc)


def load_state(path: Path | None = None) -> DailyState | None:
    """Load persisted state, or ``None`` if the file is absent / empty."""
    target = path if path is not None else DEFAULT_STATE_PATH
    if not target.exists():
        return None
    raw = target.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    blob = json.loads(raw)
    return DailyState(
        session_open_balance=D(blob["session_open_balance"]),
        last_reset_utc=datetime.fromisoformat(blob["last_reset_utc"]),
    )


def write_state(state: DailyState, path: Path | None = None) -> Path:
    target = path if path is not None else DEFAULT_STATE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    blob = {
        "session_open_balance": format(state.session_open_balance, "f"),
        "last_reset_utc": _ensure_utc(state.last_reset_utc).isoformat(),
    }
    target.write_text(json.dumps(blob, indent=2) + "\n", encoding="utf-8")
    return target


def tick(
    *,
    now_utc: datetime,
    current_equity: Decimal,
    reset_tz: str,
    reset_time: str,
    path: Path | None = None,
) -> SessionInfo:
    """Refresh state if a reset boundary has passed; return SessionInfo.

    On first-ever call (no persisted state) the current equity is recorded as
    the session-open balance and ``just_reset`` is ``True``.
    """
    now_utc = _ensure_utc(now_utc)
    last_reset = compute_last_reset(
        now_utc, reset_tz=reset_tz, reset_time=reset_time
    )
    next_reset = compute_next_reset(
        now_utc, reset_tz=reset_tz, reset_time=reset_time
    )

    stored = load_state(path)
    just_reset = False
    if stored is None:
        new_state = DailyState(
            session_open_balance=D(current_equity),
            last_reset_utc=last_reset,
        )
        write_state(new_state, path)
        just_reset = True
        return SessionInfo(state=new_state, next_reset_utc=next_reset, just_reset=True)

    stored_last = _ensure_utc(stored.last_reset_utc)
    if stored_last < last_reset:
        new_state = DailyState(
            session_open_balance=D(current_equity),
            last_reset_utc=last_reset,
        )
        write_state(new_state, path)
        just_reset = True
        return SessionInfo(state=new_state, next_reset_utc=next_reset, just_reset=True)

    return SessionInfo(state=stored, next_reset_utc=next_reset, just_reset=just_reset)


def default_daily_state_path(account_id: Optional[str] = None) -> Path:
    """Resolve the daily-state path for an account_id, or the legacy root path.

    With account_id: ~/.trading-agent-skills/accounts/<id>/daily_state.json
    Without: ~/.trading-agent-skills/daily_state.json (backwards-compat for manual use)
    """
    base = Path.home() / ".trading-agent-skills"
    if account_id:
        from trading_agent_skills.account_paths import resolve_account_paths

        return resolve_account_paths(account_id=account_id).daily_state
    return base / "daily_state.json"


__all__ = [
    "DEFAULT_STATE_PATH",
    "DailyState",
    "SessionInfo",
    "compute_last_reset",
    "compute_next_reset",
    "default_daily_state_path",
    "load_state",
    "write_state",
    "tick",
]
