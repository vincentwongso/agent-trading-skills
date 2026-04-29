"""Tests for ``cfd_skills.config_io``."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from cfd_skills.config_io import (
    SCHEMA_VERSION,
    Config,
    default_config,
    dump_config,
    load_config,
    write_config,
)


def test_default_config_matches_user_stated_defaults() -> None:
    cfg = default_config()
    # Risk discipline locked-in 2026-04-29:
    assert cfg.risk.per_trade_max_pct == Decimal("1.0")
    assert cfg.risk.daily_loss_cap_pct == Decimal("5.0")
    assert cfg.risk.caution_threshold_pct_of_cap == Decimal("50.0")
    assert cfg.risk.concurrent_risk_budget_pct == Decimal("5.0")
    assert cfg.risk.margin_warning_pct == Decimal("30.0")
    # Session reset anchor: NY 4pm ET = 6am AEST.
    assert cfg.session.reset_tz == "America/New_York"
    assert cfg.session.reset_time == "16:00"
    assert cfg.session.display_tz == "Australia/Sydney"
    # Watchlist:
    assert cfg.watchlist.default == ("XAUUSD", "XAGUSD", "USOIL", "UKOIL", "NAS100")
    assert "BTCUSD" in cfg.watchlist.base_universe
    assert cfg.schema_version == SCHEMA_VERSION


def test_load_missing_file_returns_defaults(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "absent.toml")
    assert cfg == default_config()
    # Default mode does NOT write — keeps tests / one-shot reads side-effect-free.
    assert not (tmp_path / "absent.toml").exists()


def test_load_missing_file_with_auto_write_persists_defaults(tmp_path: Path) -> None:
    """The CLIs opt into auto-write so the user has a file to customize.
    Regression guard for the smoke-test bug where ``~/.cfd-skills/config.toml``
    was never created on first invocation."""
    target = tmp_path / "subdir" / "config.toml"  # parent dir absent too
    cfg = load_config(target, write_default_if_missing=True)
    assert cfg == default_config()
    assert target.exists()
    # And the written file round-trips cleanly.
    reloaded = load_config(target)
    assert reloaded == default_config()


def test_round_trip_default_config(tmp_path: Path) -> None:
    target = tmp_path / "config.toml"
    write_config(default_config(), target)
    loaded = load_config(target)
    assert loaded == default_config()


def test_partial_user_edits_merge_over_defaults(tmp_path: Path) -> None:
    """A user editing daily_loss_cap_pct shouldn't have to restate everything."""
    target = tmp_path / "config.toml"
    target.write_text(
        '\n'.join([
            'schema_version = 1',
            '[risk]',
            'daily_loss_cap_pct = 4.0',
            '[watchlist]',
            'default = ["XAUUSD", "NAS100"]',
        ]),
        encoding="utf-8",
    )
    cfg = load_config(target)
    assert cfg.risk.daily_loss_cap_pct == Decimal("4.0")
    # Other risk fields fall back to defaults
    assert cfg.risk.per_trade_max_pct == Decimal("1.0")
    assert cfg.risk.concurrent_risk_budget_pct == Decimal("5.0")
    # Watchlist override applies
    assert cfg.watchlist.default == ("XAUUSD", "NAS100")
    # Watchlist.base_universe falls back to defaults
    assert "USOIL" in cfg.watchlist.base_universe


def test_dump_config_renders_floats_for_decimals_for_human_edit() -> None:
    """The user edits config.toml by hand — Decimals must serialise as floats,
    not stringified Decimal('1.0') gibberish."""
    rendered = dump_config(default_config())
    assert "per_trade_max_pct = 1.0" in rendered
    assert "daily_loss_cap_pct = 5.0" in rendered
    assert "Decimal" not in rendered  # no Python repr leakage


def test_dump_config_round_trips_via_tomllib(tmp_path: Path) -> None:
    target = tmp_path / "config.toml"
    cfg = default_config()
    write_config(cfg, target)
    # File is human-readable TOML the user can edit.
    text = target.read_text(encoding="utf-8")
    assert "[risk]" in text
    assert "[session]" in text
    assert 'reset_tz = "America/New_York"' in text


def test_load_config_coerces_int_pct_to_decimal(tmp_path: Path) -> None:
    """Some users write ``daily_loss_cap_pct = 5`` (int) — accept it."""
    target = tmp_path / "config.toml"
    target.write_text("[risk]\ndaily_loss_cap_pct = 5\n", encoding="utf-8")
    cfg = load_config(target)
    assert cfg.risk.daily_loss_cap_pct == Decimal("5")


def test_default_config_path_is_under_home_dotdir() -> None:
    from cfd_skills.config_io import DEFAULT_CONFIG_PATH

    assert DEFAULT_CONFIG_PATH.name == "config.toml"
    assert DEFAULT_CONFIG_PATH.parent.name == ".cfd-skills"


def test_config_is_immutable() -> None:
    cfg = default_config()
    with pytest.raises(Exception):  # frozen dataclass → FrozenInstanceError
        cfg.risk.daily_loss_cap_pct = Decimal("99")  # type: ignore[misc]
