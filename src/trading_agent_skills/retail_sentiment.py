"""FXSSI retail sentiment crowdedness scoring.

Sibling to :mod:`trading_agent_skills.cot_crowdedness`. Fills the gap where
CFTC COT doesn't apply — `GER40`, `UKOIL` (ICE not CFTC), exotic FX, crypto
CFDs, and any symbol where retail-broker positioning is the only
contrarian-positioning signal available.

The signal is **weaker than COT** (retail flow is noisier than reportable
managed-money positioning) — the crowded-fade playbook calls for halving
position size when this provider is the sole crowdedness source.

Design boundaries
-----------------
- **Pure functions** (`compute_crowdedness`, `_count_growing`) take a list of
  ``RetailSentimentEntry`` records and return a ``Crowdedness`` snapshot
  (reusing the dataclass from :mod:`cot_crowdedness` so blends are trivial).
- **Live fetcher** (`fetch_fxssi`) uses httpx against FXSSI's web sentiment
  endpoint. **The exact endpoint is unverified** — see ``fetch_fxssi`` for
  the TODO.
- **Cache** under ``~/.trading-agent-skills/retail_sentiment_cache/<symbol>.json``.
- Implements the :class:`cot_crowdedness.CrowdednessProvider` protocol via
  :class:`FxssiProvider` so the consumer (Stage 1 / Stage 2) can swap or
  blend with the COT provider.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
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

# TODO(retail_sentiment): FXSSI does NOT publish a documented public JSON API.
# This URL pattern is a best-effort guess. Confirm the actual endpoint
# (likely scrape-then-parse) before relying on `fetch_fxssi` in production.
DEFAULT_ENDPOINT = "https://fxssi.com/api/sentiment"


# ---------- Symbol → FXSSI symbol-slug map ---------------------------------

# TODO(retail_sentiment): verify each FXSSI slug against the live site.
# Some slugs (GER40 → DE30, UKOIL → BRENT, US30 → DJ30, NAS100 → NASDAQ100,
# SPX500 → SP500) are conventional FXSSI/MT5-broker naming but unverified.
# Leaving as identity for FX majors; will likely need adjustment after
# confirming the actual endpoint contract.
FXSSI_SYMBOL_MAP: dict[str, str] = {
    "GER40":  "DE30",
    "UKOIL":  "BRENT",
    "XAUUSD": "XAUUSD",
    "XAGUSD": "XAGUSD",
    "USOIL":  "WTI",
    "NAS100": "NASDAQ100",
    "SPX500": "SP500",
    "US30":   "DJ30",
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

    timestamp: datetime           # tz-aware UTC
    symbol: str                   # our symbol (e.g. "GER40"), not FXSSI's slug
    pct_long: Decimal             # 0..100
    pct_short: Decimal            # 0..100
    source: str = "fxssi"

    @classmethod
    def from_fxssi(cls, symbol: str, row: dict[str, Any]) -> "RetailSentimentEntry":
        """Parse a FXSSI JSON row of the form
        ``{"long": "62.3", "short": "37.7", "timestamp": "..."}``.

        Raises ``ValueError`` if required keys are missing.
        """
        try:
            long_raw = row["long"]
            short_raw = row["short"]
        except KeyError as exc:
            raise ValueError(
                f"FXSSI row missing required key {exc!s}; got keys={list(row)}"
            ) from exc

        ts_raw = row.get("timestamp") or row.get("time") or row.get("date")
        if ts_raw is None:
            ts = datetime.now(timezone.utc)
        elif isinstance(ts_raw, datetime):
            ts = ts_raw
        else:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        return cls(
            timestamp=ts,
            symbol=symbol,
            pct_long=D(long_raw),
            pct_short=D(short_raw),
            source="fxssi",
        )


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
    """
    if side == "neutral" or len(pcts) < 2:
        return 0
    effective = min(window, len(pcts) - 1)
    recent = pcts[-(effective + 1):]
    deltas = [recent[i + 1] - recent[i] for i in range(effective)]
    if side == "crowded_long":
        return sum(1 for d in deltas if d > 0)
    # crowded_short: pcts here is pct_short series; growing = increasing.
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


# ---------- Live fetcher (FXSSI sentiment) ---------------------------------


def fetch_fxssi(
    symbol: str,
    *,
    endpoint: str = DEFAULT_ENDPOINT,
    timeout: float = 20.0,
) -> list[RetailSentimentEntry]:
    """Pull current retail-sentiment snapshot for one symbol from FXSSI.

    .. warning::
        FXSSI does NOT publish a documented public JSON API. This fetcher is
        **best-effort** — it tries ``<endpoint>/<fxssi_slug>`` and expects a
        JSON shape of ``{"long": "62.3", "short": "37.7", "timestamp": "..."}``
        (or a list of such rows).

        If the shape doesn't match, the function raises a clear ``ValueError``.
        Once FXSSI's actual public surface is confirmed (likely an HTML scrape
        or a different endpoint path), only the URL and the row-parsing
        function should need updating — the signature is stable.

    Returns oldest-first (list of one for the snapshot endpoint, or a series
    if the endpoint returns a history array).
    """
    import httpx  # lazy import — only fetcher needs it

    if symbol not in FXSSI_SYMBOL_MAP:
        raise ValueError(f"{symbol} has no FXSSI mapping (see FXSSI_SYMBOL_MAP)")
    slug = FXSSI_SYMBOL_MAP[symbol]
    url = f"{endpoint.rstrip('/')}/{slug}"

    resp = httpx.get(url, timeout=timeout)
    resp.raise_for_status()
    body = resp.json()

    if isinstance(body, dict):
        rows = [body]
    elif isinstance(body, list):
        rows = body
    else:
        raise ValueError(
            f"FXSSI response shape unexpected for {symbol}: type={type(body).__name__}"
        )

    if not rows:
        raise ValueError(f"FXSSI returned empty payload for {symbol} at {url}")

    entries = [RetailSentimentEntry.from_fxssi(symbol, r) for r in rows]
    return sorted(entries, key=lambda e: e.timestamp)


def refresh_symbol(
    symbol: str,
    *,
    endpoint: str = DEFAULT_ENDPOINT,
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> tuple[Path, int]:
    """Fetch + cache one symbol's retail-sentiment snapshot. Returns
    ``(path, n_entries)``.

    On a refresh, we MERGE the new snapshot(s) with the existing cache so
    history accumulates over time (FXSSI exposes the current snapshot, not
    a backfill — the growing-side filter needs prior samples).
    """
    if symbol not in FXSSI_SYMBOL_MAP:
        raise ValueError(f"{symbol} has no FXSSI mapping")

    new_entries = fetch_fxssi(symbol, endpoint=endpoint)
    existing = load_cache(symbol, cache_dir=cache_dir)

    seen: set[str] = {e.timestamp.isoformat() for e in existing}
    merged = list(existing)
    for e in new_entries:
        key = e.timestamp.isoformat()
        if key not in seen:
            merged.append(e)
            seen.add(key)

    path = save_cache(symbol, merged, cache_dir=cache_dir)
    return path, len(merged)


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
    "compute_crowdedness",
    "cache_path",
    "save_cache",
    "load_cache",
    "fetch_fxssi",
    "refresh_symbol",
    "DEFAULT_CACHE_DIR",
    "DEFAULT_ENDPOINT",
    "DEFAULT_LONG_THRESHOLD",
    "DEFAULT_SHORT_THRESHOLD",
    "DEFAULT_GROWING_WINDOW",
]
