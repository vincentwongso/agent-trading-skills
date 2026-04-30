"""Tests for ``trading_agent_skills.daily_state`` — NY-close session bookkeeping."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from trading_agent_skills.daily_state import (
    DailyState,
    compute_last_reset,
    compute_next_reset,
    load_state,
    tick,
    write_state,
)


# ---------- compute_last_reset / compute_next_reset ------------------------


def test_last_reset_just_after_4pm_ny_returns_today_4pm() -> None:
    # 2026-04-29 21:00 UTC = 17:00 EDT (NY is UTC-4 in April)
    now = datetime(2026, 4, 29, 21, 0, tzinfo=timezone.utc)
    last = compute_last_reset(now, reset_tz="America/New_York", reset_time="16:00")
    # Most recent reset = today's 16:00 EDT = 20:00 UTC
    assert last == datetime(2026, 4, 29, 20, 0, tzinfo=timezone.utc)


def test_last_reset_just_before_4pm_ny_returns_yesterday_4pm() -> None:
    # 2026-04-29 19:00 UTC = 15:00 EDT — before today's reset
    now = datetime(2026, 4, 29, 19, 0, tzinfo=timezone.utc)
    last = compute_last_reset(now, reset_tz="America/New_York", reset_time="16:00")
    assert last == datetime(2026, 4, 28, 20, 0, tzinfo=timezone.utc)


def test_last_reset_handles_dst_spring_forward() -> None:
    # 2026 US DST starts 2026-03-08. Before: NY = UTC-5. After: NY = UTC-4.
    # On 2026-03-09 (Mon) at 19:00 UTC = 15:00 EDT — before reset.
    # Last reset was 2026-03-08 16:00 EDT = 2026-03-08 20:00 UTC (DST already on).
    pre_dst_now = datetime(2026, 3, 6, 19, 0, tzinfo=timezone.utc)  # Fri pre-DST
    last_pre = compute_last_reset(
        pre_dst_now, reset_tz="America/New_York", reset_time="16:00"
    )
    # 2026-03-05 16:00 EST = 21:00 UTC
    assert last_pre == datetime(2026, 3, 5, 21, 0, tzinfo=timezone.utc)

    # Post-DST equivalent: 2026-03-10 19:00 UTC = 15:00 EDT — before today's reset.
    post_dst_now = datetime(2026, 3, 10, 19, 0, tzinfo=timezone.utc)
    last_post = compute_last_reset(
        post_dst_now, reset_tz="America/New_York", reset_time="16:00"
    )
    # 2026-03-09 16:00 EDT = 20:00 UTC
    assert last_post == datetime(2026, 3, 9, 20, 0, tzinfo=timezone.utc)
    # Reset moment is 1 hour earlier in UTC after spring-forward — confirms
    # zoneinfo is doing wall-clock arithmetic, not absolute-UTC arithmetic.
    assert last_pre.time() != last_post.time()


def test_next_reset_is_24h_after_last_reset_in_local_time() -> None:
    now = datetime(2026, 4, 29, 21, 0, tzinfo=timezone.utc)
    last = compute_last_reset(now, reset_tz="America/New_York", reset_time="16:00")
    nxt = compute_next_reset(now, reset_tz="America/New_York", reset_time="16:00")
    # Outside DST boundaries, next reset is exactly 24h later in UTC.
    assert nxt - last == timedelta(hours=24)


def test_compute_last_reset_rejects_naive_datetime() -> None:
    naive = datetime(2026, 4, 29, 21, 0)
    with pytest.raises(ValueError, match="naive"):
        compute_last_reset(naive, reset_tz="America/New_York", reset_time="16:00")


def test_aest_view_aligns_with_6am_local() -> None:
    """Sanity check: NY 4pm = AEST 6am next morning during EDT."""
    from zoneinfo import ZoneInfo

    last = compute_last_reset(
        datetime(2026, 4, 29, 21, 0, tzinfo=timezone.utc),
        reset_tz="America/New_York",
        reset_time="16:00",
    )
    aest = last.astimezone(ZoneInfo("Australia/Sydney"))
    # April 30 06:00 AEST = April 29 20:00 UTC = April 29 16:00 EDT.
    assert aest.hour == 6
    assert aest.minute == 0


# ---------- load_state / write_state ---------------------------------------


def test_load_state_returns_none_for_missing_file(tmp_path: Path) -> None:
    assert load_state(tmp_path / "missing.json") is None


def test_write_then_load_round_trips(tmp_path: Path) -> None:
    target = tmp_path / "daily_state.json"
    state = DailyState(
        session_open_balance=Decimal("12345.67"),
        last_reset_utc=datetime(2026, 4, 29, 20, 0, tzinfo=timezone.utc),
    )
    write_state(state, target)
    loaded = load_state(target)
    assert loaded == state


def test_write_state_creates_parent_directory(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "dir" / "state.json"
    write_state(
        DailyState(
            session_open_balance=Decimal("1000"),
            last_reset_utc=datetime(2026, 4, 29, 20, 0, tzinfo=timezone.utc),
        ),
        target,
    )
    assert target.exists()


def test_load_state_handles_empty_file(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    target.write_text("", encoding="utf-8")
    assert load_state(target) is None


# ---------- tick ------------------------------------------------------------


def test_tick_first_invocation_records_session_open(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    info = tick(
        now_utc=datetime(2026, 4, 29, 21, 0, tzinfo=timezone.utc),
        current_equity=Decimal("10000.00"),
        reset_tz="America/New_York",
        reset_time="16:00",
        path=target,
    )
    assert info.just_reset is True
    assert info.state.session_open_balance == Decimal("10000.00")
    assert target.exists()


def test_tick_within_session_does_not_reset(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    # Seed: session opened today at 16:00 EDT with balance 10000.
    write_state(
        DailyState(
            session_open_balance=Decimal("10000.00"),
            last_reset_utc=datetime(2026, 4, 29, 20, 0, tzinfo=timezone.utc),
        ),
        target,
    )
    # Tick three hours later with new equity — must NOT reset.
    info = tick(
        now_utc=datetime(2026, 4, 29, 23, 0, tzinfo=timezone.utc),
        current_equity=Decimal("10250.00"),  # up $250
        reset_tz="America/New_York",
        reset_time="16:00",
        path=target,
    )
    assert info.just_reset is False
    assert info.state.session_open_balance == Decimal("10000.00")


def test_tick_after_reset_boundary_snapshots_new_session_open(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    # Yesterday's session.
    write_state(
        DailyState(
            session_open_balance=Decimal("10000.00"),
            last_reset_utc=datetime(2026, 4, 28, 20, 0, tzinfo=timezone.utc),
        ),
        target,
    )
    # Now is one hour after today's 16:00 EDT reset.
    info = tick(
        now_utc=datetime(2026, 4, 29, 21, 0, tzinfo=timezone.utc),
        current_equity=Decimal("10250.00"),
        reset_tz="America/New_York",
        reset_time="16:00",
        path=target,
    )
    assert info.just_reset is True
    assert info.state.session_open_balance == Decimal("10250.00")
    assert info.state.last_reset_utc == datetime(
        2026, 4, 29, 20, 0, tzinfo=timezone.utc
    )


def test_tick_persists_decimal_without_scientific_notation(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    tick(
        now_utc=datetime(2026, 4, 29, 21, 0, tzinfo=timezone.utc),
        current_equity=Decimal("0.00012345"),
        reset_tz="America/New_York",
        reset_time="16:00",
        path=target,
    )
    raw = target.read_text(encoding="utf-8")
    assert "0.00012345" in raw
    assert "E-" not in raw and "e-" not in raw
