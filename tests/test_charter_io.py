from pathlib import Path

import pytest

from trading_agent_skills.account_paths import resolve_account_paths
from trading_agent_skills.charter_io import (
    Charter,
    CharterError,
    HEARTBEAT_BY_STYLE,
    LOCKED_FIELDS,
    parse_charter,
    render_charter,
    write_charter,
    write_charter_with_archive,
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


def test_rejects_empty_account_id() -> None:
    bad = _VALID_CHARTER.replace("account_id: 12345678", "account_id:")
    with pytest.raises(CharterError, match="account_id"):
        parse_charter(bad)


def test_rejects_empty_mode() -> None:
    bad = _VALID_CHARTER.replace("mode: demo", "mode:")
    with pytest.raises(CharterError, match="mode"):
        parse_charter(bad)


def test_rejects_non_numeric_charter_version() -> None:
    bad = _VALID_CHARTER.replace("charter_version: 1", "charter_version: abc")
    with pytest.raises(CharterError, match="charter_version"):
        parse_charter(bad)


def test_rejects_non_numeric_balance() -> None:
    bad = _VALID_CHARTER.replace("created_account_balance: 10000.00", "created_account_balance: $10k")
    with pytest.raises(CharterError, match="created_account_balance"):
        parse_charter(bad)


def test_accepts_tab_indented_hard_caps() -> None:
    tab_charter = _VALID_CHARTER.replace(
        "hard_caps:\n  per_trade_risk_pct: 1.0\n  daily_loss_pct: 5.0\n  max_concurrent_positions: 3",
        "hard_caps:\n\tper_trade_risk_pct: 1.0\n\tdaily_loss_pct: 5.0\n\tmax_concurrent_positions: 3",
    )
    c = parse_charter(tab_charter)
    assert c.hard_caps.per_trade_risk_pct == 1.0


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
    archived = parse_charter((paths.charter_versions / "v1.md").read_text(encoding="utf-8"))
    assert archived.charter_version == 1


def test_archive_refuses_version_mismatch(tmp_path: Path) -> None:
    paths = resolve_account_paths(account_id="12345678", base=tmp_path)
    paths.ensure_dirs()
    v1 = parse_charter(_VALID_CHARTER)
    write_charter(paths.charter, v1)
    # Re-write at version 1 (no bump) — must refuse
    with pytest.raises(CharterError, match="must increment"):
        write_charter_with_archive(paths, v1)


def test_render_rejects_quote_in_notes() -> None:
    c = parse_charter(_VALID_CHARTER)
    bad = Charter(
        mode=c.mode, account_id=c.account_id, heartbeat=c.heartbeat,
        hard_caps=c.hard_caps, charter_version=c.charter_version,
        created_at=c.created_at, created_account_balance=c.created_account_balance,
        trading_style=c.trading_style, sessions_allowed=c.sessions_allowed,
        instruments=c.instruments, allowed_setups=c.allowed_setups,
        notes='He said "hi"',
    )
    with pytest.raises(CharterError, match="notes"):
        render_charter(bad)


def test_render_rejects_newline_in_notes() -> None:
    c = parse_charter(_VALID_CHARTER)
    bad = Charter(
        mode=c.mode, account_id=c.account_id, heartbeat=c.heartbeat,
        hard_caps=c.hard_caps, charter_version=c.charter_version,
        created_at=c.created_at, created_account_balance=c.created_account_balance,
        trading_style=c.trading_style, sessions_allowed=c.sessions_allowed,
        instruments=c.instruments, allowed_setups=c.allowed_setups,
        notes="line1\nline2",
    )
    with pytest.raises(CharterError, match="notes"):
        render_charter(bad)


def test_render_rejects_comma_in_instrument() -> None:
    c = parse_charter(_VALID_CHARTER)
    bad = Charter(
        mode=c.mode, account_id=c.account_id, heartbeat=c.heartbeat,
        hard_caps=c.hard_caps, charter_version=c.charter_version,
        created_at=c.created_at, created_account_balance=c.created_account_balance,
        trading_style=c.trading_style, sessions_allowed=c.sessions_allowed,
        instruments=["XAU,USD"], allowed_setups=c.allowed_setups,
        notes=c.notes,
    )
    with pytest.raises(CharterError, match="instruments"):
        render_charter(bad)


def test_write_charter_creates_parent_dir(tmp_path: Path) -> None:
    paths = resolve_account_paths(account_id="12345678", base=tmp_path)
    # Do NOT call ensure_dirs — write_charter should create the parent itself.
    c = parse_charter(_VALID_CHARTER)
    write_charter(paths.charter, c)
    assert paths.charter.is_file()
