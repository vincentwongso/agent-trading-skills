"""Per-symbol EWMA spread baseline persisted to disk.

The pre-trade checklist compares the current spread against a baseline. If
spread is meaningfully wider (e.g. > 2× baseline), that's a WARN — it usually
indicates thin liquidity or impending news.

Baselines are exponentially-weighted moving averages, persisted in
``~/.trading-agent-skills/spread_baseline.json``:

    {
      "schema_version": 1,
      "alpha": "0.1",
      "baselines": {
        "XAUUSD": {"ewma": "12.5", "samples": 47, "updated_utc": "..."},
        ...
      }
    }

``alpha = 0.1`` ⇒ baseline gives ~63% weight to the most recent 10 samples.
Bootstrapped on first sample (no prior history → baseline = current spread).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from trading_agent_skills.decimal_io import D


SCHEMA_VERSION = 1
DEFAULT_BASELINE_PATH = Path.home() / ".trading-agent-skills" / "spread_baseline.json"
DEFAULT_ALPHA = Decimal("0.1")


@dataclass(frozen=True)
class Baseline:
    symbol: str
    ewma: Decimal
    samples: int
    updated_utc: datetime


@dataclass
class BaselineStore:
    """Mutable in-memory view of the on-disk baseline file."""
    alpha: Decimal
    baselines: dict[str, Baseline]

    @classmethod
    def load(cls, path: Path | None = None) -> "BaselineStore":
        target = path if path is not None else DEFAULT_BASELINE_PATH
        if not target.exists():
            return cls(alpha=DEFAULT_ALPHA, baselines={})
        raw = target.read_text(encoding="utf-8").strip()
        if not raw:
            return cls(alpha=DEFAULT_ALPHA, baselines={})
        blob = json.loads(raw)
        alpha = D(blob.get("alpha", DEFAULT_ALPHA))
        baselines: dict[str, Baseline] = {}
        for symbol, b in blob.get("baselines", {}).items():
            baselines[symbol] = Baseline(
                symbol=symbol,
                ewma=D(b["ewma"]),
                samples=int(b["samples"]),
                updated_utc=datetime.fromisoformat(b["updated_utc"]),
            )
        return cls(alpha=alpha, baselines=baselines)

    def save(self, path: Path | None = None) -> Path:
        target = path if path is not None else DEFAULT_BASELINE_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        blob: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "alpha": format(self.alpha, "f"),
            "baselines": {
                s: {
                    "ewma": format(b.ewma, "f"),
                    "samples": b.samples,
                    "updated_utc": b.updated_utc.isoformat(),
                }
                for s, b in sorted(self.baselines.items())
            },
        }
        target.write_text(json.dumps(blob, indent=2) + "\n", encoding="utf-8")
        return target

    def update(
        self,
        symbol: str,
        current_spread_pts: Decimal,
        *,
        now_utc: datetime,
    ) -> Baseline:
        """Apply ``alpha`` to the baseline; bootstrap if no prior sample."""
        current = D(current_spread_pts)
        prior = self.baselines.get(symbol)
        if prior is None:
            new = Baseline(
                symbol=symbol,
                ewma=current,
                samples=1,
                updated_utc=now_utc,
            )
        else:
            new_ewma = self.alpha * current + (Decimal("1") - self.alpha) * prior.ewma
            new = Baseline(
                symbol=symbol,
                ewma=new_ewma,
                samples=prior.samples + 1,
                updated_utc=now_utc,
            )
        self.baselines[symbol] = new
        return new

    def get(self, symbol: str) -> Optional[Baseline]:
        return self.baselines.get(symbol)


def ratio_vs_baseline(current_spread_pts: Decimal, baseline: Baseline) -> Decimal:
    """Current / baseline, in EWMA units. > 1 means wider than usual."""
    if baseline.ewma <= 0:
        return Decimal("1")
    return D(current_spread_pts) / baseline.ewma


__all__ = [
    "SCHEMA_VERSION",
    "DEFAULT_BASELINE_PATH",
    "DEFAULT_ALPHA",
    "Baseline",
    "BaselineStore",
    "ratio_vs_baseline",
]
