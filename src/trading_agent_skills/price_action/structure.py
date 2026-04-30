"""Higher-level structural derivations from pivot lists.

- S/R levels: cluster nearby pivots into a single level (price = mean of
  cluster, ``tested`` = count).
- Regime per timeframe: derived from the last few pivots' HH/HL/LH/LL
  labels (trend_up / trend_down / range / transition).
- MTF alignment: combine per-TF regimes into a single classification
  (aligned_long / aligned_short / mixed / conflicted).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from trading_agent_skills.price_action.pivots import Pivot


RegimeKind = Literal["trend_up", "trend_down", "range", "transition"]
SRSide = Literal["support", "resistance"]
MTFAlignment = Literal["aligned_long", "aligned_short", "mixed", "conflicted"]


@dataclass(frozen=True)
class SRLevel:
    price: Decimal
    side: SRSide
    tf: str
    tested: int
    last_test_index: int


def cluster_sr_levels(
    pivots: list[Pivot],
    *,
    tick_size: Decimal,
    cluster_factor: int,
    tf: str,
) -> list[SRLevel]:
    """Group pivots whose prices fall within ``tick_size * cluster_factor``.

    Highs and lows are clustered separately. Each cluster yields one
    ``SRLevel`` whose price is the arithmetic mean of cluster members.
    """
    if not pivots:
        return []
    band = tick_size * Decimal(cluster_factor)

    def _cluster(group: list[Pivot], side: SRSide) -> list[SRLevel]:
        if not group:
            return []
        sorted_g = sorted(group, key=lambda p: p.price)
        clusters: list[list[Pivot]] = [[sorted_g[0]]]
        for p in sorted_g[1:]:
            if p.price - clusters[-1][-1].price <= band:
                clusters[-1].append(p)
            else:
                clusters.append([p])
        out: list[SRLevel] = []
        for c in clusters:
            mean = sum((p.price for p in c), start=Decimal("0")) / Decimal(len(c))
            last_test = max(p.index for p in c)
            out.append(SRLevel(
                price=mean, side=side, tf=tf,
                tested=len(c), last_test_index=last_test,
            ))
        return out

    highs = [p for p in pivots if p.kind == "swing_high"]
    lows = [p for p in pivots if p.kind == "swing_low"]
    return _cluster(highs, "resistance") + _cluster(lows, "support")


def classify_regime(pivots: list[Pivot]) -> RegimeKind:
    """Read the last ~6 labelled pivots to classify regime.

    Rules:
    - All recent labels in {HH, HL} → trend_up
    - All recent labels in {LH, LL} → trend_down
    - Mixed (one counter-direction label among >=3 prior aligned labels) -> transition
    - Otherwise → range
    """
    labelled = [p for p in pivots if p.label is not None][-6:]
    if len(labelled) < 2:
        return "range"
    labels = [p.label for p in labelled]
    up_set = {"HH", "HL"}
    down_set = {"LH", "LL"}
    n_up = sum(1 for l in labels if l in up_set)
    n_down = sum(1 for l in labels if l in down_set)
    if n_down == 0:
        return "trend_up"
    if n_up == 0:
        return "trend_down"
    last = labels[-1]
    prior = labels[:-1]
    if len(prior) >= 3:
        if last in down_set and all(l in up_set for l in prior):
            return "transition"
        if last in up_set and all(l in down_set for l in prior):
            return "transition"
    return "range"


def classify_mtf_alignment(regime_by_tf: dict[str, RegimeKind]) -> MTFAlignment:
    """Reduce per-TF regimes to one of four overall states."""
    if not regime_by_tf:
        return "conflicted"
    regimes = list(regime_by_tf.values())
    if all(r == "trend_up" for r in regimes):
        return "aligned_long"
    if all(r == "trend_down" for r in regimes):
        return "aligned_short"
    has_up = any(r == "trend_up" for r in regimes)
    has_down = any(r == "trend_down" for r in regimes)
    if has_up and has_down:
        return "conflicted"
    return "mixed"


__all__ = [
    "RegimeKind",
    "SRSide",
    "MTFAlignment",
    "SRLevel",
    "cluster_sr_levels",
    "classify_regime",
    "classify_mtf_alignment",
]
