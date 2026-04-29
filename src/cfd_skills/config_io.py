"""Read/write ``~/.cfd-skills/config.toml``.

The config carries the user's stated risk discipline (daily cap, concurrent
budget, etc.), session-reset anchor, and watchlist defaults. The plan keeps
it deliberately small — API keys never live here (they're env vars), and
swap rates are broker-driven (read live from MT5).

This module is pure read/serialise. Interactive first-run prompts and
natural-language edits ("set my daily cap to 4%") are agent-side concerns
documented in SKILL.md; the agent uses ``dump_config`` to render the file.
"""

from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import tomli_w

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


SCHEMA_VERSION = 1

DEFAULT_CONFIG_PATH = Path.home() / ".cfd-skills" / "config.toml"


def _d(value: Any) -> Decimal:
    """Coerce TOML scalar (float / int / str) to Decimal via ``str``.

    Distinct from ``decimal_io.D`` which rejects floats: TOML literally
    parses ``5.0`` as a Python float, and that's a deliberate choice for
    config-file ergonomics. Going through ``str`` keeps ``5.0`` exact.
    """
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


@dataclass(frozen=True)
class RiskConfig:
    per_trade_max_pct: Decimal
    daily_loss_cap_pct: Decimal
    caution_threshold_pct_of_cap: Decimal
    concurrent_risk_budget_pct: Decimal
    margin_warning_pct: Decimal


@dataclass(frozen=True)
class SessionConfig:
    reset_tz: str
    reset_time: str  # "HH:MM" 24h, in reset_tz
    display_tz: str


@dataclass(frozen=True)
class WatchlistConfig:
    default: tuple[str, ...]
    base_universe: tuple[str, ...]
    max_size: int


@dataclass(frozen=True)
class NewsConfig:
    dedup_similarity_threshold: Decimal
    cache_seconds: int


@dataclass(frozen=True)
class IndicatorsConfig:
    atr_period: int
    rsi_period: int
    rsi_oversold: int
    rsi_overbought: int


@dataclass(frozen=True)
class Config:
    schema_version: int
    risk: RiskConfig
    session: SessionConfig
    watchlist: WatchlistConfig
    news: NewsConfig
    indicators: IndicatorsConfig


def default_config() -> Config:
    """Stated defaults from the user (locked in 2026-04-29)."""
    return Config(
        schema_version=SCHEMA_VERSION,
        risk=RiskConfig(
            per_trade_max_pct=Decimal("1.0"),
            daily_loss_cap_pct=Decimal("5.0"),
            caution_threshold_pct_of_cap=Decimal("50.0"),
            concurrent_risk_budget_pct=Decimal("5.0"),
            margin_warning_pct=Decimal("30.0"),
        ),
        session=SessionConfig(
            reset_tz="America/New_York",
            reset_time="16:00",
            display_tz="Australia/Sydney",
        ),
        watchlist=WatchlistConfig(
            default=("XAUUSD", "XAGUSD", "USOIL", "UKOIL", "NAS100"),
            base_universe=(
                "XAUUSD", "XAGUSD", "USOIL", "UKOIL", "NAS100",
                "US500", "US30", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD",
            ),
            max_size=8,
        ),
        news=NewsConfig(
            dedup_similarity_threshold=Decimal("0.85"),
            cache_seconds=60,
        ),
        indicators=IndicatorsConfig(
            atr_period=14,
            rsi_period=14,
            rsi_oversold=30,
            rsi_overbought=70,
        ),
    )


def _config_from_dict(blob: dict[str, Any]) -> Config:
    base = default_config()
    schema_version = int(blob.get("schema_version", base.schema_version))
    risk_blob = blob.get("risk", {})
    session_blob = blob.get("session", {})
    watchlist_blob = blob.get("watchlist", {})
    news_blob = blob.get("news", {})
    indicators_blob = blob.get("indicators", {})

    risk = replace(
        base.risk,
        **{
            k: _d(risk_blob[k])
            for k in (
                "per_trade_max_pct",
                "daily_loss_cap_pct",
                "caution_threshold_pct_of_cap",
                "concurrent_risk_budget_pct",
                "margin_warning_pct",
            )
            if k in risk_blob
        },
    )

    session = replace(
        base.session,
        **{
            k: str(session_blob[k])
            for k in ("reset_tz", "reset_time", "display_tz")
            if k in session_blob
        },
    )

    watchlist_overrides: dict[str, Any] = {}
    if "default" in watchlist_blob:
        watchlist_overrides["default"] = tuple(watchlist_blob["default"])
    if "base_universe" in watchlist_blob:
        watchlist_overrides["base_universe"] = tuple(watchlist_blob["base_universe"])
    if "max_size" in watchlist_blob:
        watchlist_overrides["max_size"] = int(watchlist_blob["max_size"])
    watchlist = replace(base.watchlist, **watchlist_overrides)

    news_overrides: dict[str, Any] = {}
    if "dedup_similarity_threshold" in news_blob:
        news_overrides["dedup_similarity_threshold"] = _d(
            news_blob["dedup_similarity_threshold"]
        )
    if "cache_seconds" in news_blob:
        news_overrides["cache_seconds"] = int(news_blob["cache_seconds"])
    news = replace(base.news, **news_overrides)

    indicators_overrides: dict[str, Any] = {}
    for k in ("atr_period", "rsi_period", "rsi_oversold", "rsi_overbought"):
        if k in indicators_blob:
            indicators_overrides[k] = int(indicators_blob[k])
    indicators = replace(base.indicators, **indicators_overrides)

    return Config(
        schema_version=schema_version,
        risk=risk,
        session=session,
        watchlist=watchlist,
        news=news,
        indicators=indicators,
    )


def load_config(
    path: Path | None = None,
    *,
    write_default_if_missing: bool = False,
) -> Config:
    """Load config from ``path`` (defaults to ``~/.cfd-skills/config.toml``).

    Returns ``default_config()`` if the file is missing. When
    ``write_default_if_missing=True`` the defaults are also persisted to disk
    so the user has a file to customize. CLIs use that mode; tests pass
    explicit paths and skip the auto-write.
    """
    target = path if path is not None else DEFAULT_CONFIG_PATH
    if not target.exists():
        cfg = default_config()
        if write_default_if_missing:
            try:
                write_config(cfg, target)
            except OSError:
                # Non-fatal — still return defaults so the CLI can run.
                pass
        return cfg
    with target.open("rb") as f:
        blob = tomllib.load(f)
    return _config_from_dict(blob)


def _config_to_dict(cfg: Config) -> dict[str, Any]:
    """TOML-serialisable shape; Decimals rendered as floats for human edit."""
    def dec_to_float(d: Decimal) -> float:
        return float(d)

    return {
        "schema_version": cfg.schema_version,
        "risk": {
            "per_trade_max_pct": dec_to_float(cfg.risk.per_trade_max_pct),
            "daily_loss_cap_pct": dec_to_float(cfg.risk.daily_loss_cap_pct),
            "caution_threshold_pct_of_cap": dec_to_float(
                cfg.risk.caution_threshold_pct_of_cap
            ),
            "concurrent_risk_budget_pct": dec_to_float(
                cfg.risk.concurrent_risk_budget_pct
            ),
            "margin_warning_pct": dec_to_float(cfg.risk.margin_warning_pct),
        },
        "session": asdict(cfg.session),
        "watchlist": {
            "default": list(cfg.watchlist.default),
            "base_universe": list(cfg.watchlist.base_universe),
            "max_size": cfg.watchlist.max_size,
        },
        "news": {
            "dedup_similarity_threshold": dec_to_float(
                cfg.news.dedup_similarity_threshold
            ),
            "cache_seconds": cfg.news.cache_seconds,
        },
        "indicators": asdict(cfg.indicators),
    }


def dump_config(cfg: Config) -> str:
    """Render config as TOML (the format the user edits by hand)."""
    return tomli_w.dumps(_config_to_dict(cfg))


def write_config(cfg: Config, path: Path | None = None) -> Path:
    """Write config to ``path`` (defaults to ``~/.cfd-skills/config.toml``).

    Creates the parent directory if needed. Returns the resolved path.
    """
    target = path if path is not None else DEFAULT_CONFIG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dump_config(cfg), encoding="utf-8")
    return target


# Re-export for callers wanting a quick override without re-importing replace
__all__ = [
    "SCHEMA_VERSION",
    "DEFAULT_CONFIG_PATH",
    "RiskConfig",
    "SessionConfig",
    "WatchlistConfig",
    "NewsConfig",
    "IndicatorsConfig",
    "Config",
    "default_config",
    "load_config",
    "dump_config",
    "write_config",
]
