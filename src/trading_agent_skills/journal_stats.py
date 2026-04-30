"""Pure analytics over resolved journal entries.

Definitions used here:

  - **Net trade outcome** = ``realized_pnl + swap_accrued + commission``.
    This is the cash that hit the account, after financing and fees.
  - **Win** = net outcome > 0. **Loss** = net outcome < 0. Breakeven (= 0)
    counts toward the trade count but not the win/loss split.
  - **R-multiple** = net outcome / original_risk_amount. The journal
    requires ``original_risk_amount`` to be non-zero on every open entry,
    so this never divides by zero.
  - **Expectancy per trade** = total net P&L / trade count.
  - **Swing-trade subset** = trades where ``|swap_accrued| > 0.2 ×
    |realized_pnl|`` — a heuristic for surfacing carry-driven trades
    distinctly from directional trades.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any, Callable, Iterable

from trading_agent_skills.decimal_io import D


@dataclass
class Summary:
    count: int = 0
    win_count: int = 0
    loss_count: int = 0
    breakeven_count: int = 0
    win_rate: Decimal = field(default_factory=lambda: Decimal("0"))
    total_pnl: Decimal = field(default_factory=lambda: Decimal("0"))
    realized_pnl_total: Decimal = field(default_factory=lambda: Decimal("0"))
    swap_pnl_total: Decimal = field(default_factory=lambda: Decimal("0"))
    commission_total: Decimal = field(default_factory=lambda: Decimal("0"))
    avg_r_multiple: Decimal = field(default_factory=lambda: Decimal("0"))
    expectancy_per_trade: Decimal = field(default_factory=lambda: Decimal("0"))

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        for k, v in list(out.items()):
            if isinstance(v, Decimal):
                out[k] = format(v, "f")
        return out


def _net(entry: dict) -> Decimal:
    return D(entry["realized_pnl"]) + D(entry["swap_accrued"]) + D(entry["commission"])


def compute_summary(entries: Iterable[dict]) -> Summary:
    """Aggregate stats over a flat list of resolved entries."""
    entries_list = list(entries)
    summary = Summary(count=len(entries_list))
    if not entries_list:
        return summary

    r_sum = Decimal("0")
    for e in entries_list:
        net = _net(e)
        summary.total_pnl += net
        summary.realized_pnl_total += D(e["realized_pnl"])
        summary.swap_pnl_total += D(e["swap_accrued"])
        summary.commission_total += D(e["commission"])
        if net > 0:
            summary.win_count += 1
        elif net < 0:
            summary.loss_count += 1
        else:
            summary.breakeven_count += 1
        risk = D(e["original_risk_amount"])
        if risk > 0:
            r_sum += net / risk

    decided = summary.win_count + summary.loss_count
    if decided > 0:
        summary.win_rate = (
            Decimal(summary.win_count) / Decimal(decided) * Decimal("100")
        )
    summary.avg_r_multiple = r_sum / Decimal(summary.count)
    summary.expectancy_per_trade = summary.total_pnl / Decimal(summary.count)
    return summary


def compute_grouped(
    entries: Iterable[dict],
    *,
    key: Callable[[dict], str],
) -> dict[str, Summary]:
    """Group entries by ``key(entry)`` and compute a Summary per group."""
    bucket: dict[str, list[dict]] = {}
    for e in entries:
        bucket.setdefault(key(e), []).append(e)
    return {k: compute_summary(v) for k, v in bucket.items()}


def by_setup_type(entries: Iterable[dict]) -> dict[str, Summary]:
    return compute_grouped(entries, key=lambda e: e.get("setup_type") or "(untagged)")


def by_symbol(entries: Iterable[dict]) -> dict[str, Summary]:
    return compute_grouped(entries, key=lambda e: e["symbol"])


def by_side(entries: Iterable[dict]) -> dict[str, Summary]:
    return compute_grouped(entries, key=lambda e: e["side"])


def by_risk_classification(entries: Iterable[dict]) -> dict[str, Summary]:
    return compute_grouped(
        entries,
        key=lambda e: e.get("risk_classification_at_close") or "UNKNOWN",
    )


def swing_subset(
    entries: Iterable[dict],
    *,
    swap_dominance_threshold: Decimal = Decimal("0.2"),
) -> list[dict]:
    """Trades where swap drove a meaningful share of the outcome.

    Heuristic: ``|swap_accrued| > threshold × |realized_pnl|``. With the
    default threshold of 0.2, a trade that earned $100 directional and
    +$25 swap qualifies; a $1000 directional with +$50 swap doesn't.
    Edge case: if ``realized_pnl == 0``, the trade is a swing trade by
    definition (no directional component, only carry).
    """
    out: list[dict] = []
    for e in entries:
        swap = D(e["swap_accrued"])
        directional = D(e["realized_pnl"])
        if directional == 0 and swap != 0:
            out.append(e)
            continue
        if abs(swap) > swap_dominance_threshold * abs(directional):
            out.append(e)
    return out
