"""Detector base types: ``CandidateSetup`` + ``EntryZone``.

Each detector module exposes ``detect(bundle, structure) -> list[CandidateSetup]``.
The registry (populated as detectors are added) lives in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


SetupType = Literal[
    "pullback_ema",
    "sr_bounce",
    "pin_bar",
    "engulfing",
    "range_break_retest",
    "fvg_fill",
    "ob_retest",
    "liq_sweep",
    "bos_pullback",
]
Side = Literal["long", "short"]


@dataclass(frozen=True)
class EntryZone:
    low: Decimal
    high: Decimal

    def __post_init__(self) -> None:
        if self.low > self.high:
            raise ValueError(
                f"EntryZone low {self.low} must be <= high {self.high}"
            )


@dataclass(frozen=True)
class CandidateSetup:
    type: SetupType
    tf_setup: str
    tf_trigger: str
    side: Side
    entry_zone: EntryZone
    suggested_entry: Decimal
    invalidation: Decimal
    targets: tuple[Decimal, ...]
    confluence: tuple[str, ...]
    candle_quality: Decimal  # [0, 1] — detector-supplied
    narrative_hint: str

    @property
    def stop_distance(self) -> Decimal:
        return abs(self.suggested_entry - self.invalidation)


__all__ = ["SetupType", "Side", "EntryZone", "CandidateSetup"]
