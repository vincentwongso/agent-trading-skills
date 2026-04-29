"""MTF bar bundle — thin wrapper around per-timeframe ``indicators.Bar`` lists.

Reuses ``cfd_skills.indicators.Bar`` (do not create a parallel Bar class —
the EMA/ATR routines already accept that type, and divergence here would
silently break consumers).

A ``Timeframe`` is one of MT5's nine canonical codes: M1, M5, M15, M30,
H1, H4, D1, W1, MN1. ``MTFBars`` carries a sorted-ascending dict and
exposes safe lookups + recent-window slices.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from cfd_skills.indicators import Bar, bars_from_mcp


Timeframe = Literal["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"]


_TIMEFRAME_MINUTES: dict[str, int] = {
    "M1": 1, "M5": 5, "M15": 15, "M30": 30,
    "H1": 60, "H4": 240,
    "D1": 1440, "W1": 10080, "MN1": 43200,
}


def parse_timeframe(code: str) -> Timeframe:
    if code not in _TIMEFRAME_MINUTES:
        raise ValueError(f"unknown timeframe {code!r}")
    return code  # type: ignore[return-value]


def timeframe_minutes(tf: Timeframe | str) -> int:
    if tf not in _TIMEFRAME_MINUTES:
        raise ValueError(f"unknown timeframe {tf!r}")
    return _TIMEFRAME_MINUTES[tf]


@dataclass(frozen=True)
class MTFBars:
    """Sorted-ascending mapping ``Timeframe → list[Bar]``."""

    bars_by_tf: dict[str, list[Bar]] = field(default_factory=dict)

    @classmethod
    def from_bundle(cls, blob: dict[str, list[dict[str, Any]]]) -> "MTFBars":
        out: dict[str, list[Bar]] = {}
        for tf, entries in blob.items():
            parse_timeframe(tf)  # validates
            out[tf] = bars_from_mcp(entries)
        return cls(bars_by_tf=out)

    def timeframes(self) -> tuple[str, ...]:
        """Return TFs sorted ascending by minute count (M1 first, MN1 last)."""
        return tuple(sorted(self.bars_by_tf.keys(), key=timeframe_minutes))

    def bars(self, tf: str) -> list[Bar]:
        if tf not in self.bars_by_tf:
            raise KeyError(f"no bars for timeframe {tf!r}")
        return self.bars_by_tf[tf]

    def recent(self, tf: str, n: int) -> list[Bar]:
        bs = self.bars(tf)
        if n <= 0:
            return []
        return bs[-n:]


__all__ = [
    "Timeframe",
    "MTFBars",
    "parse_timeframe",
    "timeframe_minutes",
]
