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


def _match_editorial_to_broker(
    editorial: str, broker_catalog: Iterable[str]
) -> tuple[str, ...]:
    """Find broker symbols matching an editorial root.

    Editorial entry ``XAUUSD`` matches broker ``XAUUSD`` exactly, or any
    suffixed form like ``XAUUSD.z`` / ``XAUUSD.r``. Match is case-insensitive
    on the editorial root; the returned strings preserve the broker's casing
    so they round-trip through MCP cleanly.
    """
    editorial_upper = editorial.upper()
    matches: list[str] = []
    for broker_sym in broker_catalog:
        b_upper = broker_sym.upper()
        if b_upper == editorial_upper or b_upper.startswith(editorial_upper + "."):
            matches.append(broker_sym)
    return tuple(matches)


def symbols_for_currencies(
    currencies: Iterable[str],
    *,
    base_universe: Iterable[str] | None = None,
) -> tuple[str, ...]:
    """Map a set of currency codes to their liquid CFD symbols.

    If ``base_universe`` is provided, the editorial symbols are translated
    to broker form via prefix match — ``XAUUSD`` editorial finds ``XAUUSD.z``
    in the catalog. This keeps the calendar tier from emitting names the
    broker doesn't actually offer (which would silently dead-end downstream
    when the orchestrator looks up bars/meta).
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

    catalog = tuple(base_universe)
    out: list[str] = []
    out_seen: set[str] = set()
    for editorial in raw:
        for broker_sym in _match_editorial_to_broker(editorial, catalog):
            key = broker_sym.upper()
            if key not in out_seen:
                out_seen.add(key)
                out.append(broker_sym)
    return tuple(out)


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
        key = sym.upper()
        if key not in seen:
            seen.add(key)
            out.append(sym)
    # If any earnings entries were flagged for index constituents, surface
    # the indices themselves. The caller passes the *index symbols* (e.g.
    # ["NAS100"]) — actual constituent → index mapping is editorial. Same
    # broker-catalog prefix match as above so ``NAS100`` in editorial finds
    # ``NAS100.cash`` etc. if the broker uses suffix form.
    catalog = tuple(base_universe) if base_universe is not None else None
    for idx in earnings_constituents_for_indices:
        idx_upper = idx.upper()
        if idx_upper not in _INDEX_TO_CURRENCIES:
            continue
        if catalog is None:
            if idx_upper not in seen:
                seen.add(idx_upper)
                out.append(idx_upper)
        else:
            for broker_sym in _match_editorial_to_broker(idx, catalog):
                key = broker_sym.upper()
                if key not in seen:
                    seen.add(key)
                    out.append(broker_sym)
    return tuple(out)


def resolve_watchlist(
    *,
    explicit: Iterable[str] | None = None,
    open_position_symbols: Iterable[str] | None = None,
    calendar_symbols: Iterable[str] | None = None,
    volatility_ranked: Iterable[str] | None = None,
    default: Iterable[str],
    max_size: int = 8,
    broker_catalog: Iterable[str] | None = None,
) -> WatchlistResolution:
    """Merge in priority order, dedupe (preserving first-seen position), cap.

    When ``broker_catalog`` is provided, editorial-form names in the
    ``explicit`` / ``volatility`` / ``default`` tiers are translated to their
    broker-form counterparts via prefix-match (e.g. editorial ``XAUUSD`` →
    broker ``XAUUSD.z``). ``calendar_symbols`` and ``open_position_symbols``
    are assumed to already be broker-form (the news CLI applies the same
    prefix-match in ``calendar_driven_symbols`` upstream; positions come
    from the broker directly). Without the catalog, all tiers pass through
    as-is — preserving back-compat for callers that don't have a catalog.
    """
    if max_size <= 0:
        raise ValueError(f"max_size must be > 0, got {max_size}")

    if broker_catalog is not None:
        catalog = tuple(broker_catalog)
        explicit_tier = _translate_to_broker(_normalise(explicit), catalog)
        volatility_tier = _translate_to_broker(_normalise(volatility_ranked), catalog)
        default_tier = _translate_to_broker(_normalise(default), catalog)
    else:
        explicit_tier = _normalise(explicit)
        volatility_tier = _normalise(volatility_ranked)
        default_tier = _normalise(default)

    tier_inputs: list[tuple[str, tuple[str, ...]]] = [
        ("explicit", explicit_tier),
        ("open_positions", _normalise(open_position_symbols)),
        ("calendar", _normalise(calendar_symbols)),
        ("volatility", volatility_tier),
        ("default", default_tier),
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


def _translate_to_broker(
    syms: Iterable[str], catalog: tuple[str, ...]
) -> tuple[str, ...]:
    """Map editorial symbols to broker-form via the catalog.

    Order of precedence per editorial entry:
      1. Exact case-insensitive match in the catalog → keep with broker casing.
      2. Prefix match (``XAUUSD`` finds ``XAUUSD.z``) → emit each broker match
         in catalog order.
      3. No match → drop the symbol entirely. The news CLI excludes such
         symbols from swing-candidate evaluation anyway because there's no
         bar data, so leaving them in the watchlist only confuses the user.
    """
    catalog_by_upper: dict[str, str] = {b.upper(): b for b in catalog}
    out: list[str] = []
    seen: set[str] = set()
    for s in syms:
        s_upper = s.upper()
        if s_upper in catalog_by_upper:
            broker_sym = catalog_by_upper[s_upper]
            if broker_sym.upper() not in seen:
                seen.add(broker_sym.upper())
                out.append(broker_sym)
            continue
        for broker_sym in _match_editorial_to_broker(s, catalog):
            key = broker_sym.upper()
            if key not in seen:
                seen.add(key)
                out.append(broker_sym)
    return tuple(out)


def _normalise(syms: Optional[Iterable[str]]) -> tuple[str, ...]:
    """Trim, drop empties, dedupe case-insensitively while preserving the
    first-seen original case. Broker symbol form is the source of truth —
    `XAUUSD.z` and `XAUUSD.Z` collapse, but the caller's casing wins."""
    if syms is None:
        return ()
    out: list[str] = []
    seen_keys: set[str] = set()
    for s in syms:
        original = str(s).strip()
        if not original:
            continue
        key = original.upper()
        if key not in seen_keys:
            seen_keys.add(key)
            out.append(original)
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
