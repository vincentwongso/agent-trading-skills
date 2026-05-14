"""Pure-function enrichment + finding for Calix calendar payloads.

Inputs are raw Calix JSON dicts plus an injected ``now_utc`` so tests stay
deterministic. Outputs are augmented dicts with computed time fields. No I/O,
no module-level state, no Decimal (calendar deals in dates and strings).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


_SYDNEY = ZoneInfo("Australia/Sydney")  # auto-handles AEST/AEDT


def _parse_iso(s: str) -> datetime:
    """Calix returns ISO-8601 with trailing 'Z'; Python <3.11 doesn't grok 'Z'."""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _format_local(scheduled: datetime) -> str:
    local = scheduled.astimezone(_SYDNEY)
    return local.strftime("%Y-%m-%d %H:%M %Z")


def _enrich_economic(event: dict, now_utc: datetime) -> dict:
    enriched = dict(event)
    scheduled = _parse_iso(event["scheduledAt"])
    delta_minutes = int((scheduled - now_utc).total_seconds() // 60)
    enriched["minutes_until"] = delta_minutes
    enriched["minutes_since"] = -delta_minutes if delta_minutes < 0 else 0
    enriched["is_past"] = delta_minutes < 0
    enriched["local_time_aest"] = _format_local(scheduled)
    enriched["actual_present"] = event.get("actual") is not None
    return enriched


def enrich_events(payload: dict[str, Any], *, now_utc: datetime) -> dict[str, Any]:
    """Add computed time + presence fields to each event in a Calix payload.

    Output preserves all upstream fields and adds:
      - now_utc, fetched_at_utc (top-level)
      - degraded (top-level, mirrors stale)
      - per-event: minutes_until, minutes_since, is_past, local_time_aest,
        actual_present
    """
    iso_now = now_utc.isoformat()
    out = dict(payload)
    out["now_utc"] = iso_now
    out["fetched_at_utc"] = iso_now
    out["degraded"] = bool(payload.get("stale", False))
    if "events" in payload:
        out["events"] = [_enrich_economic(e, now_utc) for e in payload["events"]]
    return out
