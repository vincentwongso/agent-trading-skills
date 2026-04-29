"""5-tier watchlist resolver for ``session-news-brief``.

Resolution order (deduped union, capped at ``max_size``):
  1. **Explicit input** — user passed a watchlist into the skill
  2. **Open positions** — symbols you currently have skin in
  3. **Calendar-driven** — symbols whose currencies map to high-impact
     economic / earnings events in the lookahead window
  4. **Volatility-ranked** — top-N from the configured ``base_universe``
     by ATR-as-percentage-of-price, computed externally (passed in by the
     orchestrator so this module stays pure)
  5. **Static fallback** — configured ``default_watchlist``

Each tier is logged in the result so the brief can show *why* a symbol
appeared. Useful when the user asks "why is XAGUSD on my list this morning?".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from cfd_skills.symbol_meta import _INDEX_TO_CURRENCIES


# Currency → liquid CFD symbols (for the calendar-driven tier).
# Editorial mapping; the user can override base_universe in config.toml.
_CURRENCY_TO_SYMBOLS: dict[str, tuple[str, ...]] = {
    "USD": ("XAUUSD", "XAGUSD", "USOIL", "NAS100", "US500", "EURUSD", "GBPUSD", "USDJPY"),
    "EUR": ("EURUSD", "GER40"),
    "GBP": ("GBPUSD", "UKOIL", "UK100"),
    "JPY": ("USDJPY", "JPN225"),
    "AUD": ("AUDUSD", "AUS200"),
    "CHF": ("USDCHF",),
    "CAD": ("USDCAD",),
    "NZD": ("NZDUSD",),
}


@dataclass(frozen=True)
class WatchlistResolution:
    symbols: tuple[str, ...]               # final deduped, capped
    by_tier: dict[str, tuple[str, ...]]    # contribution per tier
    description: str                       # short human-readable summary

    @property
    def primary_tier(self) -> str:
        for tier, syms in self.by_tier.items():
            if syms:
                return tier
        return "default"


def symbols_for_currencies(
    currencies: Iterable[str],
    *,
    base_universe: Iterable[str] | None = None,
) -> tuple[str, ...]:
    """Map a set of currency codes to their liquid CFD symbols.

    If ``base_universe`` is provided, results are filtered to it — this
    keeps "I trade XAUUSD/UKOIL/NAS100" users from seeing JPY pairs they
    don't follow.
    """
    raw: list[str] = []
    seen: set[str] = set()
    for ccy in currencies:
        ccy_upper = ccy.upper()
        for sym in _CURRENCY_TO_SYMBOLS.get(ccy_upper, ()):
            if sym not in seen:
                seen.add(sym)
                raw.append(sym)
    if base_universe is None:
        return tuple(raw)
    base_set = {s.upper() for s in base_universe}
    return tuple(s for s in raw if s.upper() in base_set)


def calendar_driven_symbols(
    *,
    economic_event_currencies: Iterable[str],
    earnings_constituents_for_indices: Iterable[str],
    base_universe: Iterable[str] | None = None,
) -> tuple[str, ...]:
    """Compose calendar-driven candidates from economic + earnings inputs."""
    out: list[str] = []
    seen: set[str] = set()
    for sym in symbols_for_currencies(
        economic_event_currencies, base_universe=base_universe
    ):
        if sym not in seen:
            seen.add(sym)
            out.append(sym)
    # If any earnings entries were flagged for index constituents, surface
    # the indices themselves. The caller passes the *index symbols* (e.g.
    # ["NAS100"]) — actual constituent → index mapping is editorial.
    for idx in earnings_constituents_for_indices:
        idx_upper = idx.upper()
        if idx_upper in _INDEX_TO_CURRENCIES and idx_upper not in seen:
            if base_universe is None or idx_upper in {s.upper() for s in base_universe}:
                seen.add(idx_upper)
                out.append(idx_upper)
    return tuple(out)


def resolve_watchlist(
    *,
    explicit: Iterable[str] | None = None,
    open_position_symbols: Iterable[str] | None = None,
    calendar_symbols: Iterable[str] | None = None,
    volatility_ranked: Iterable[str] | None = None,
    default: Iterable[str],
    max_size: int = 8,
) -> WatchlistResolution:
    """Merge in priority order, dedupe (preserving first-seen position), cap."""
    if max_size <= 0:
        raise ValueError(f"max_size must be > 0, got {max_size}")

    tier_inputs: list[tuple[str, tuple[str, ...]]] = [
        ("explicit", _normalise(explicit)),
        ("open_positions", _normalise(open_position_symbols)),
        ("calendar", _normalise(calendar_symbols)),
        ("volatility", _normalise(volatility_ranked)),
        ("default", _normalise(default)),
    ]

    by_tier: dict[str, tuple[str, ...]] = {}
    chosen: list[str] = []
    seen: set[str] = set()
    for tier, syms in tier_inputs:
        contributed: list[str] = []
        for s in syms:
            if len(chosen) >= max_size:
                break
            if s in seen:
                continue
            seen.add(s)
            chosen.append(s)
            contributed.append(s)
        by_tier[tier] = tuple(contributed)
        if len(chosen) >= max_size:
            # Still record empty contributions for downstream tiers so the
            # description is consistent.
            for remaining_tier, _ in tier_inputs[tier_inputs.index((tier, syms)) + 1:]:
                by_tier.setdefault(remaining_tier, ())
            break

    description = _build_description(by_tier, len(chosen), max_size)
    return WatchlistResolution(
        symbols=tuple(chosen),
        by_tier=by_tier,
        description=description,
    )


def _normalise(syms: Optional[Iterable[str]]) -> tuple[str, ...]:
    if syms is None:
        return ()
    out: list[str] = []
    seen: set[str] = set()
    for s in syms:
        u = str(s).upper().strip()
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return tuple(out)


def _build_description(
    by_tier: dict[str, tuple[str, ...]],
    total: int,
    max_size: int,
) -> str:
    parts: list[str] = []
    for tier, syms in by_tier.items():
        if syms:
            label = {
                "explicit": "explicit",
                "open_positions": "open positions",
                "calendar": "calendar",
                "volatility": "volatility",
                "default": "default",
            }.get(tier, tier)
            parts.append(f"{len(syms)} from {label}")
    if not parts:
        return "empty (no symbols found)"
    return f"{total}/{max_size} symbols: " + ", ".join(parts)


__all__ = [
    "WatchlistResolution",
    "symbols_for_currencies",
    "calendar_driven_symbols",
    "resolve_watchlist",
]
