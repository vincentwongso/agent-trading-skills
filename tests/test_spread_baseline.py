"""Tests for ``cfd_skills.spread_baseline`` — EWMA + on-disk persistence."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from cfd_skills.spread_baseline import (
    DEFAULT_ALPHA,
    Baseline,
    BaselineStore,
    ratio_vs_baseline,
)


# ---------- bootstrapping --------------------------------------------------


def test_first_sample_bootstraps_baseline_to_current(tmp_path: Path) -> None:
    store = BaselineStore.load(tmp_path / "absent.json")
    now = datetime(2026, 4, 29, 21, 0, tzinfo=timezone.utc)
    b = store.update("XAUUSD", Decimal("12.5"), now_utc=now)
    assert b.ewma == Decimal("12.5")
    assert b.samples == 1
    assert b.updated_utc == now


def test_load_returns_empty_when_file_missing(tmp_path: Path) -> None:
    store = BaselineStore.load(tmp_path / "absent.json")
    assert store.alpha == DEFAULT_ALPHA
    assert store.baselines == {}


# ---------- EWMA -----------------------------------------------------------


def test_ewma_blends_current_and_prior_with_alpha() -> None:
    store = BaselineStore(alpha=Decimal("0.1"), baselines={})
    now = datetime(2026, 4, 29, 21, 0, tzinfo=timezone.utc)
    store.update("XAUUSD", Decimal("10"), now_utc=now)
    # alpha=0.1: new = 0.1 * 30 + 0.9 * 10 = 3 + 9 = 12
    b = store.update("XAUUSD", Decimal("30"), now_utc=now)
    assert b.ewma == Decimal("12.0")
    assert b.samples == 2


def test_ewma_converges_toward_steady_value() -> None:
    store = BaselineStore(alpha=Decimal("0.1"), baselines={})
    now = datetime(2026, 4, 29, 21, 0, tzinfo=timezone.utc)
    store.update("XAUUSD", Decimal("20"), now_utc=now)
    for _ in range(50):
        store.update("XAUUSD", Decimal("20"), now_utc=now)
    b = store.get("XAUUSD")
    assert b is not None
    assert b.ewma == Decimal("20")  # steady-state convergence


# ---------- per-symbol isolation -------------------------------------------


def test_baselines_are_per_symbol(tmp_path: Path) -> None:
    store = BaselineStore.load(tmp_path / "missing.json")
    now = datetime(2026, 4, 29, 21, 0, tzinfo=timezone.utc)
    store.update("XAUUSD", Decimal("10"), now_utc=now)
    store.update("UKOIL", Decimal("3"), now_utc=now)
    assert store.get("XAUUSD").ewma == Decimal("10")  # type: ignore[union-attr]
    assert store.get("UKOIL").ewma == Decimal("3")  # type: ignore[union-attr]


# ---------- persistence ----------------------------------------------------


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    target = tmp_path / "spread_baseline.json"
    store = BaselineStore.load(target)
    now = datetime(2026, 4, 29, 21, 0, tzinfo=timezone.utc)
    store.update("XAUUSD", Decimal("12.5"), now_utc=now)
    store.update("UKOIL", Decimal("3.2"), now_utc=now)
    store.save(target)

    reloaded = BaselineStore.load(target)
    assert reloaded.get("XAUUSD") == Baseline(
        symbol="XAUUSD",
        ewma=Decimal("12.5"),
        samples=1,
        updated_utc=now,
    )
    assert reloaded.get("UKOIL") == Baseline(
        symbol="UKOIL",
        ewma=Decimal("3.2"),
        samples=1,
        updated_utc=now,
    )


def test_save_creates_parent_directory(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "dir" / "baseline.json"
    store = BaselineStore.load(target)
    now = datetime(2026, 4, 29, 21, 0, tzinfo=timezone.utc)
    store.update("XAUUSD", Decimal("12.5"), now_utc=now)
    store.save(target)
    assert target.exists()


def test_save_avoids_scientific_notation(tmp_path: Path) -> None:
    target = tmp_path / "baseline.json"
    store = BaselineStore.load(target)
    now = datetime(2026, 4, 29, 21, 0, tzinfo=timezone.utc)
    store.update("XAUUSD", Decimal("0.00012345"), now_utc=now)
    store.save(target)
    raw = target.read_text(encoding="utf-8")
    assert "0.00012345" in raw
    assert "E-" not in raw


# ---------- ratio_vs_baseline ----------------------------------------------


def test_ratio_2x_when_current_is_double() -> None:
    b = Baseline(
        symbol="XAUUSD",
        ewma=Decimal("12.5"),
        samples=10,
        updated_utc=datetime(2026, 4, 29, 21, 0, tzinfo=timezone.utc),
    )
    r = ratio_vs_baseline(Decimal("25.0"), b)
    assert r == Decimal("2")


def test_ratio_returns_one_when_baseline_zero() -> None:
    b = Baseline(
        symbol="XAUUSD",
        ewma=Decimal("0"),
        samples=0,
        updated_utc=datetime(2026, 4, 29, 21, 0, tzinfo=timezone.utc),
    )
    assert ratio_vs_baseline(Decimal("5"), b) == Decimal("1")
