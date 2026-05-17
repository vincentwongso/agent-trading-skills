"""FXSSI retail sentiment crowdedness scoring.

Sibling to :mod:`trading_agent_skills.cot_crowdedness`. Fills the gap where
CFTC COT doesn't apply — `GER40`, `UKOIL` (ICE not CFTC), exotic FX, crypto
CFDs, and any symbol where retail-broker positioning is the only
contrarian-positioning signal available.

The signal is **weaker than COT** (retail flow is noisier than reportable
managed-money positioning) — the crowded-fade playbook calls for halving
position size when this provider is the sole crowdedness source.

Data source
-----------
``https://fxssi.com/api/current-ratios`` returns one JSON blob with every
mapped pair at once. Schema verified 2026-05-17::

    {
      "pairs": {
        "XAUUSD": {
          "amarkets": "66.70", "dukscopy": "70.53", "fxssi": "82.44",
          ...,
          "average": "74.82"     # ← pct_long averaged across brokers
        },
        ...
      },
      "server_time": 1779013021,    # request-side unix epoch
      "formed":      1779012607,    # data-assembly unix epoch (used as ts)
      "broker_titles": {...}, "broker_weights": {...}, ...
    }

``average`` is the mean *percent-long* across the reporting brokers; we
derive ``pct_short = 100 - average``. One call refreshes every symbol — no
per-symbol fan-out needed.

Design boundaries
-----------------
- **Pure functions** (``compute_crowdedness``, ``parse_response``, ``_count_growing``)
  take a list of ``RetailSentimentEntry`` records (or a raw JSON dict) and
  return a ``Crowdedness`` snapshot (reusing the dataclass from
  :mod:`cot_crowdedness` so blends are trivial).
- **Live fetcher** (``fetch_all_fxssi``) uses httpx against the verified
  endpoint and returns a ``{our_symbol: RetailSentimentEntry}`` dict.
- **Cache** under ``~/.trading-agent-skills/retail_sentiment_cache/<symbol>.json``.
- Implements the :class:`cot_crowdedness.CrowdednessProvider` protocol via
  :class:`FxssiProvider` so the consumer (Stage 1 / Stage 2) can swap or
  blend with the COT provider.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from trading_agent_skills.cot_crowdedness import (
    Crowdedness,
    CrowdednessProvider,  # noqa: F401  re-exported for type-checkers
    CrowdednessTag,
)
from trading_agent_skills.decimal_io import D


DEFAULT_CACHE_DIR = Path.home() / ".trading-agent-skills" / "retail_sentiment_cache"
DEFAULT_LONG_THRESHOLD = Decimal("75")
DEFAULT_SHORT_THRESHOLD = Decimal("75")
DEFAULT_GROWING_WINDOW = 4
DEFAULT_ENDPOINT = "https://fxssi.com/api/current-ratios"


# ---------- Symbol → FXSSI symbol-slug map ---------------------------------

# Slugs verified 2026-05-17 against live ``/api/current-ratios`` payload
# (25 pairs available). Symbols not listed below have no FXSSI coverage on
# the public endpoint.
FXSSI_SYMBOL_MAP: dict[str, str] = {
    "GER40":  "GER40",
    "UKOIL":  "XBRUSD",
    "XAUUSD": "XAUUSD",
    "XAGUSD": "XAGUSD",
    "USOIL":  "XTIUSD",
    "NAS100": "NAS100",
    "SPX500": "SP500",
    "US30":   "US30",
    "EURUSD": "EURUSD",
    "GBPUSD": "GBPUSD",
    "USDJPY": "USDJPY",
    "AUDUSD": "AUDUSD",
    "USDCAD": "USDCAD",
    "EURGBP": "EURGBP",
    "BTCUSD": "BTCUSD",
}


# ---------- Domain types ---------------------------------------------------


@dataclass(frozen=True)
class RetailSentimentEntry:
    """One snapshot of retail long/short percentages for a single symbol."""

    timestamp: datetime           # tz-aware UTC (FXSSI ``formed`` epoch)
    symbol: str                   # our symbol (e.g. "GER40"), not FXSSI's slug
    pct_long: Decimal             # 0..100
    pct_short: Decimal            # 0..100
    source: str = "fxssi"


def _parse_pair_entry(
    symbol: str,
    pair_blob: dict[str, Any],
    timestamp: datetime,
) -> RetailSentimentEntry:
    """Build a ``RetailSentimentEntry`` from one ``.pairs.<slug>`` blob.

    Uses the ``average`` field (mean pct_long across reporting brokers).
    """
    try:
        avg_long = D(pair_blob["average"])
    except KeyError as exc:
        raise ValueError(
            f"FXSSI pair blob for {symbol} missing 'average'; got keys={list(pair_blob)}"
        ) from exc
    return RetailSentimentEntry(
        timestamp=timestamp,
        symbol=symbol,
        pct_long=avg_long,
        pct_short=Decimal("100") - avg_long,
        source="fxssi",
    )


def parse_response(body: dict[str, Any]) -> dict[str, RetailSentimentEntry]:
    """Parse one ``/api/current-ratios`` response into ``{our_symbol: entry}``.

    Skips slugs not in :data:`FXSSI_SYMBOL_MAP`. Raises ``ValueError`` if the
    top-level shape is wrong.
    """
    if not isinstance(body, dict) or "pairs" not in body:
        raise ValueError(
            f"FXSSI response missing 'pairs' key; top-level keys="
            f"{list(body) if isinstance(body, dict) else type(body).__name__}"
        )
    pairs = body.get("pairs") or {}
    if not isinstance(pairs, dict):
        raise ValueError(f"FXSSI 'pairs' is not a dict; got {type(pairs).__name__}")

    formed = body.get("formed") or body.get("server_time")
    if formed is None:
        ts = datetime.now(timezone.utc)
    else:
        try:
            ts = datetime.fromtimestamp(int(formed), tz=timezone.utc)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"FXSSI 'formed' timestamp invalid: {formed!r}") from exc

    slug_to_symbol = {slug: sym for sym, slug in FXSSI_SYMBOL_MAP.items()}
    out: dict[str, RetailSentimentEntry] = {}
    for slug, pair_blob in pairs.items():
        our_sym = slug_to_symbol.get(slug)
        if our_sym is None or not isinstance(pair_blob, dict):
            continue
        try:
            out[our_sym] = _parse_pair_entry(our_sym, pair_blob, ts)
        except ValueError:
            # Skip malformed pairs — refresh of other symbols should still succeed.
            continue
    return out


# ---------- Pure scoring ---------------------------------------------------


def _count_growing(
    pcts: list[Decimal],
    *,
    side: CrowdednessTag,
    window: int = DEFAULT_GROWING_WINDOW,
) -> int:
    """Of the last ``window`` snapshots, count week-over-week deltas that
    grew the crowded side.

    Adapts to short series: if fewer than ``window+1`` samples are available
    uses all consecutive deltas. Returns 0 for ``neutral``.

    Note: callers pass the *crowded-side* pct (pct_long for crowded_long,
    pct_short for crowded_short); growing in both cases means delta > 0.
    """
    if side == "neutral" or len(pcts) < 2:
        return 0
    effective = min(window, len(pcts) - 1)
    recent = pcts[-(effective + 1):]
    deltas = [recent[i + 1] - recent[i] for i in range(effective)]
    return sum(1 for d in deltas if d > 0)


def compute_crowdedness(
    symbol: str,
    entries: list[RetailSentimentEntry],
    *,
    long_threshold: Decimal = DEFAULT_LONG_THRESHOLD,
    short_threshold: Decimal = DEFAULT_SHORT_THRESHOLD,
    growing_window: int = DEFAULT_GROWING_WINDOW,
) -> Crowdedness:
    """Score the latest retail-sentiment entry against the long/short
    thresholds (default 75%).

    Returns a :class:`Crowdedness` (the same dataclass COT uses) with:

    - ``contract_code`` = ``"fxssi:<symbol>"`` (provider-prefixed so blends
      can distinguish source).
    - ``contract_label`` = ``"FXSSI Retail Sentiment <symbol>"``.
    - ``percentile`` repurposed semantically: actual ``pct_long`` for
      ``crowded_long``, ``pct_short`` for ``crowded_short``, ``50`` for
      ``neutral``. FXSSI gives an instantaneous proportion, not a
      distribution rank — adapting the field rather than inventing a new one
      keeps blends uniform.
    - ``weeks_growing`` = count of last ``growing_window`` deltas on the
      crowded-side pct that grew (uses ``len(entries) - 1`` when the series
      is shorter than the window).
    - ``inverse`` = ``False`` always (retail sentiment is symbol-side
      already; no contract translation needed).

    Raises ``ValueError`` on empty entries or unmapped symbol.
    """
    if symbol not in FXSSI_SYMBOL_MAP:
        raise ValueError(f"{symbol} has no FXSSI mapping (see FXSSI_SYMBOL_MAP)")
    if not entries:
        raise ValueError(f"{symbol}: no retail-sentiment entries supplied")

    sorted_entries = sorted(entries, key=lambda e: e.timestamp)
    latest = sorted_entries[-1]

    if latest.pct_long >= long_threshold:
        tag: CrowdednessTag = "crowded_long"
        percentile = latest.pct_long
        side_pcts = [e.pct_long for e in sorted_entries]
    elif latest.pct_short >= short_threshold:
        tag = "crowded_short"
        percentile = latest.pct_short
        side_pcts = [e.pct_short for e in sorted_entries]
    else:
        tag = "neutral"
        percentile = Decimal("50")
        side_pcts = []

    weeks_growing = _count_growing(side_pcts, side=tag, window=growing_window)

    return Crowdedness(
        symbol=symbol,
        contract_code=f"fxssi:{symbol}",
        contract_label=f"FXSSI Retail Sentiment {symbol}",
        as_of=latest.timestamp,
        latest_net=latest.pct_long - latest.pct_short,
        percentile=percentile,
        tag=tag,
        weeks_growing=weeks_growing,
        lookback_weeks=len(sorted_entries),
        inverse=False,
    )


# ---------- Cache ----------------------------------------------------------


def cache_path(symbol: str, *, cache_dir: Path = DEFAULT_CACHE_DIR) -> Path:
    return cache_dir / f"{symbol}.json"


def save_cache(
    symbol: str,
    entries: list[RetailSentimentEntry],
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_path(symbol, cache_dir=cache_dir)
    payload = {
        "symbol": symbol,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "entries": [
            {
                "timestamp": e.timestamp.isoformat(),
                "symbol": e.symbol,
                "pct_long": str(e.pct_long),
                "pct_short": str(e.pct_short),
                "source": e.source,
            }
            for e in sorted(entries, key=lambda x: x.timestamp)
        ],
    }
    path.write_text(json.dumps(payload, indent=2))
    return path


def load_cache(
    symbol: str,
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> list[RetailSentimentEntry]:
    path = cache_path(symbol, cache_dir=cache_dir)
    if not path.exists():
        return []
    payload = json.loads(path.read_text())
    return [
        RetailSentimentEntry(
            timestamp=datetime.fromisoformat(r["timestamp"]),
            symbol=str(r.get("symbol", symbol)),
            pct_long=D(r["pct_long"]),
            pct_short=D(r["pct_short"]),
            source=str(r.get("source", "fxssi")),
        )
        for r in payload.get("entries", [])
    ]


def _merge_into_cache(
    symbol: str,
    new_entry: RetailSentimentEntry,
    *,
    cache_dir: Path,
) -> tuple[Path, int]:
    """Append ``new_entry`` to the symbol's cache if its timestamp is new.

    FXSSI exposes the current snapshot only; the ``weeks_growing`` filter
    needs accumulated history, so refreshes merge rather than overwrite.
    Returns ``(path, n_entries_after_merge)``.
    """
    existing = load_cache(symbol, cache_dir=cache_dir)
    seen = {e.timestamp.isoformat() for e in existing}
    if new_entry.timestamp.isoformat() in seen:
        merged = existing
    else:
        merged = existing + [new_entry]
    path = save_cache(symbol, merged, cache_dir=cache_dir)
    return path, len(merged)


# ---------- Live fetcher (FXSSI sentiment) ---------------------------------


def fetch_all_fxssi(
    *,
    endpoint: str = DEFAULT_ENDPOINT,
    timeout: float = 20.0,
) -> dict[str, RetailSentimentEntry]:
    """Pull current retail-sentiment for every mapped symbol in one call.

    Returns ``{our_symbol: RetailSentimentEntry}``. Symbols not present in
    the FXSSI response or not in :data:`FXSSI_SYMBOL_MAP` are absent from
    the result. Raises on HTTP error or malformed top-level shape.
    """
    import httpx  # lazy import — only fetcher needs it

    resp = httpx.get(endpoint, timeout=timeout)
    resp.raise_for_status()
    return parse_response(resp.json())


def fetch_fxssi(
    symbol: str,
    *,
    endpoint: str = DEFAULT_ENDPOINT,
    timeout: float = 20.0,
) -> list[RetailSentimentEntry]:
    """Fetch the current snapshot for one symbol.

    Thin wrapper around :func:`fetch_all_fxssi` for callers that only want
    one symbol — the bulk fetch is the same network cost either way.
    """
    if symbol not in FXSSI_SYMBOL_MAP:
        raise ValueError(f"{symbol} has no FXSSI mapping (see FXSSI_SYMBOL_MAP)")
    all_entries = fetch_all_fxssi(endpoint=endpoint, timeout=timeout)
    entry = all_entries.get(symbol)
    if entry is None:
        raise ValueError(
            f"FXSSI response did not contain {symbol} (slug {FXSSI_SYMBOL_MAP[symbol]})"
        )
    return [entry]


def refresh_symbol(
    symbol: str,
    *,
    endpoint: str = DEFAULT_ENDPOINT,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    pre_fetched: Optional[dict[str, RetailSentimentEntry]] = None,
) -> tuple[Path, int]:
    """Fetch + merge one symbol's snapshot into its cache.

    If ``pre_fetched`` is supplied (e.g. by :func:`refresh_all`), no network
    call is made — the bulk fetch is reused. Returns ``(path, n_entries)``.
    """
    if symbol not in FXSSI_SYMBOL_MAP:
        raise ValueError(f"{symbol} has no FXSSI mapping")

    if pre_fetched is None:
        bulk = fetch_all_fxssi(endpoint=endpoint)
    else:
        bulk = pre_fetched
    entry = bulk.get(symbol)
    if entry is None:
        raise ValueError(
            f"FXSSI response did not contain {symbol} (slug {FXSSI_SYMBOL_MAP[symbol]})"
        )
    return _merge_into_cache(symbol, entry, cache_dir=cache_dir)


def refresh_all(
    *,
    endpoint: str = DEFAULT_ENDPOINT,
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> dict[str, tuple[Path, int]]:
    """Single network call + per-symbol cache merge for every mapped symbol
    present in the response.

    Returns ``{our_symbol: (cache_path, n_entries)}``. Symbols absent from
    the FXSSI payload (e.g. broker outage) are silently skipped — callers
    can diff against :data:`FXSSI_SYMBOL_MAP` to detect coverage gaps.
    """
    bulk = fetch_all_fxssi(endpoint=endpoint)
    out: dict[str, tuple[Path, int]] = {}
    for symbol in bulk:
        out[symbol] = _merge_into_cache(symbol, bulk[symbol], cache_dir=cache_dir)
    return out


# ---------- Provider --------------------------------------------------------


class FxssiProvider:
    """:class:`CrowdednessProvider` implementation backed by the FXSSI cache.

    Returns ``None`` when the symbol is unmapped or no cache exists — Stage 1
    / Stage 2 should treat that as "no signal available" and not as an error.
    """

    def __init__(
        self,
        *,
        cache_dir: Path = DEFAULT_CACHE_DIR,
        long_threshold: Decimal = DEFAULT_LONG_THRESHOLD,
        short_threshold: Decimal = DEFAULT_SHORT_THRESHOLD,
    ) -> None:
        self.cache_dir = cache_dir
        self.long_threshold = long_threshold
        self.short_threshold = short_threshold

    def get_crowdedness(self, symbol: str) -> Optional[Crowdedness]:
        if symbol not in FXSSI_SYMBOL_MAP:
            return None
        entries = load_cache(symbol, cache_dir=self.cache_dir)
        if not entries:
            return None
        return compute_crowdedness(
            symbol,
            entries,
            long_threshold=self.long_threshold,
            short_threshold=self.short_threshold,
        )


__all__ = [
    "RetailSentimentEntry",
    "FxssiProvider",
    "FXSSI_SYMBOL_MAP",
    "parse_response",
    "compute_crowdedness",
    "cache_path",
    "save_cache",
    "load_cache",
    "fetch_all_fxssi",
    "fetch_fxssi",
    "refresh_symbol",
    "refresh_all",
    "DEFAULT_CACHE_DIR",
    "DEFAULT_ENDPOINT",
    "DEFAULT_LONG_THRESHOLD",
    "DEFAULT_SHORT_THRESHOLD",
    "DEFAULT_GROWING_WINDOW",
]
