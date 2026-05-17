"""CFTC Commitment of Traders (COT) crowdedness scoring.

Computes a per-symbol "is the managed-money crowd extreme right now?" tag from
the CFTC Disaggregated Futures-Only weekly report. The output drives
contrarian playbooks (see ``strategies/crowded-fade.md``).

Design boundaries
-----------------
- **Pure functions** (`compute_crowdedness`, `percentile_rank`, `tag_from_percentile`)
  take a list of `CotEntry` records and return a `Crowdedness` snapshot. No I/O,
  fully unit-testable, no live network in tests.
- **Live fetcher** (`fetch_socrata`) uses httpx against the CFTC Socrata API
  (publicreporting.cftc.gov). Free, JSON, ~156 weekly rows per symbol for a
  3y window — small payloads, no auth required.
- **Cache** under ``~/.trading-agent-skills/cot_cache/<symbol>.json`` so a
  refresh-once-per-week cron can decouple from per-Stage-2 calls.
- **Provider protocol** (`CrowdednessProvider`) — COT is the v1 provider; FXSSI
  retail-sentiment and AlphaVantage options open-interest belong as sibling
  providers under the same protocol so the consumer (Stage 1 / Stage 2) can
  blend or fall back.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Literal, Optional, Protocol

from trading_agent_skills.decimal_io import D


CrowdednessTag = Literal["crowded_long", "crowded_short", "neutral"]

DEFAULT_CACHE_DIR = Path.home() / ".trading-agent-skills" / "cot_cache"
DEFAULT_LOOKBACK_WEEKS = 156   # ~3 years
DEFAULT_LONG_THRESHOLD = Decimal("90")
DEFAULT_SHORT_THRESHOLD = Decimal("10")
DEFAULT_WEEKS_GROWING = 3      # of last 4 reports must show growth on the crowded side
DEFAULT_GROWING_WINDOW = 4

# CFTC publishes two relevant Socrata datasets — financial-futures symbols
# (FX + equity indices) are NOT in the Disaggregated commodities report.
#   - "disagg" → Disaggregated Futures Only (commodities: oil, metals, ags).
#     Speculative-crowd fields: m_money_positions_long_all / _short_all.
#   - "legacy" → Legacy Futures Only (all commodities + financials).
#     Speculative-crowd fields: noncomm_positions_long_all / _short_all.
# We use disagg for commodities (richer "managed-money" breakout) and legacy
# for financials (the only place CFTC reports them in this format).
DatasetKind = Literal["disagg", "legacy"]

SOCRATA_DISAGG = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"
SOCRATA_LEGACY = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"

_DATASET_CONFIG: dict[DatasetKind, dict[str, str]] = {
    "disagg": {
        "endpoint": SOCRATA_DISAGG,
        "long_field": "m_money_positions_long_all",
        "short_field": "m_money_positions_short_all",
    },
    "legacy": {
        "endpoint": SOCRATA_LEGACY,
        "long_field": "noncomm_positions_long_all",
        "short_field": "noncomm_positions_short_all",
    },
}

# Back-compat alias — kept so external callers that imported SOCRATA_ENDPOINT
# don't break. Prefer SOCRATA_DISAGG / SOCRATA_LEGACY in new code.
SOCRATA_ENDPOINT = SOCRATA_DISAGG


# ---------- Symbol → CFTC contract map -------------------------------------


@dataclass(frozen=True)
class CftcContract:
    code: str                  # cftc_contract_market_code (string)
    label: str                 # human-readable
    exchange: str              # e.g. NYMEX, COMEX, CME, CBT
    dataset: DatasetKind = "disagg"   # which Socrata dataset to query
    note: str = ""             # optional caveat (e.g. inverse for USDJPY)


# Codes verified against CFTC report headers (live as of 2026-05-17).
# Dataset choice: commodities use Disaggregated (managed-money breakout);
# financials use Legacy (non-commercial breakout — TFF would also work but
# coverage is identical and the legacy field is stable).
#
# `inverse` means the symbol is quoted opposite the futures contract
# (e.g. USDJPY rises when JPY futures fall) — consumers must invert the tag.
SYMBOL_TO_CFTC: dict[str, CftcContract] = {
    # Commodities — Disaggregated
    "USOIL":  CftcContract("067651", "CRUDE OIL, LIGHT SWEET",         "NYMEX", "disagg"),
    "XAUUSD": CftcContract("088691", "GOLD",                            "COMEX", "disagg"),
    "XAGUSD": CftcContract("084691", "SILVER",                          "COMEX", "disagg"),
    # Equity indices — Legacy (no Disaggregated coverage)
    "NAS100": CftcContract("209742", "E-MINI NASDAQ-100 INDEX",         "CME",   "legacy"),
    "SPX500": CftcContract("13874A", "E-MINI S&P 500",                  "CME",   "legacy"),
    "US30":   CftcContract("124603", "E-MINI DOW JONES INDUSTRIAL AVG (x$5)", "CBT", "legacy"),
    # FX majors — Legacy
    "EURUSD": CftcContract("099741", "EURO FX",                         "CME",   "legacy"),
    "GBPUSD": CftcContract("096742", "BRITISH POUND",                   "CME",   "legacy"),
    "USDJPY": CftcContract("097741", "JAPANESE YEN",                    "CME",   "legacy",
                           note="inverse: JPY-long crowd = USDJPY-short crowd"),
    "AUDUSD": CftcContract("232741", "AUSTRALIAN DOLLAR",               "CME",   "legacy"),
    "USDCAD": CftcContract("090741", "CANADIAN DOLLAR",                 "CME",   "legacy",
                           note="inverse: CAD-long crowd = USDCAD-short crowd"),
    # UKOIL (Brent) trades on ICE, not CFTC — separate provider needed.
    # GER40 has no direct CFTC contract — use EUREX or FXSSI retail sentiment.
}

INVERSE_SYMBOLS = {sym for sym, c in SYMBOL_TO_CFTC.items() if "inverse" in c.note.lower()}


# ---------- Domain types ---------------------------------------------------


@dataclass(frozen=True)
class CotEntry:
    """One weekly COT row for a single contract.

    The ``mm_long`` / ``mm_short`` fields name the original disaggregated-COT
    "managed-money" actor category, but semantically hold whichever field is
    the *speculative crowd* for the contract's dataset — managed-money for
    disagg, non-commercial for legacy. Shapiro's contrarian thesis applies to
    both; we keep the field names stable for storage compatibility.
    """
    report_date: datetime              # tz-aware UTC, the Tuesday-as-of date
    contract_code: str
    mm_long: Decimal                   # speculative-crowd long positions
    mm_short: Decimal                  # speculative-crowd short positions

    @property
    def mm_net(self) -> Decimal:
        return self.mm_long - self.mm_short

    @classmethod
    def from_socrata(
        cls,
        row: dict[str, Any],
        *,
        dataset: DatasetKind = "disagg",
    ) -> "CotEntry":
        date_raw = row.get("report_date_as_yyyy_mm_dd") or row["report_date_as_yyyy_mm_dd_text"]
        dt = (
            date_raw if isinstance(date_raw, datetime)
            else datetime.fromisoformat(str(date_raw).replace("Z", "+00:00"))
        )
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        cfg = _DATASET_CONFIG[dataset]
        return cls(
            report_date=dt,
            contract_code=str(row["cftc_contract_market_code"]),
            mm_long=D(row.get(cfg["long_field"], "0")),
            mm_short=D(row.get(cfg["short_field"], "0")),
        )


@dataclass(frozen=True)
class Crowdedness:
    symbol: str
    contract_code: str
    contract_label: str
    as_of: datetime                    # Tuesday-as-of report date
    latest_net: Decimal
    percentile: Decimal                # 0..100, latest_net vs lookback distribution
    tag: CrowdednessTag
    weeks_growing: int                 # of last GROWING_WINDOW reports, count growing on tag side
    lookback_weeks: int
    inverse: bool                      # if True, tag is for the SYMBOL (already flipped from contract)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["as_of"] = self.as_of.isoformat()
        d["latest_net"] = str(self.latest_net)
        d["percentile"] = str(self.percentile)
        return d


# ---------- Pure scoring ---------------------------------------------------


def percentile_rank(value: Decimal, distribution: list[Decimal]) -> Decimal:
    """Percentile rank (0..100) of ``value`` within ``distribution``.

    Uses the "rank" convention: percent of values <= value. Ties count as <=.
    """
    if not distribution:
        raise ValueError("distribution is empty")
    n = len(distribution)
    le = sum(1 for v in distribution if v <= value)
    return Decimal(le) * Decimal("100") / Decimal(n)


def tag_from_percentile(
    pct: Decimal,
    *,
    long_threshold: Decimal = DEFAULT_LONG_THRESHOLD,
    short_threshold: Decimal = DEFAULT_SHORT_THRESHOLD,
) -> CrowdednessTag:
    if pct >= long_threshold:
        return "crowded_long"
    if pct <= short_threshold:
        return "crowded_short"
    return "neutral"


def _count_growing(
    nets: list[Decimal],
    *,
    side: CrowdednessTag,
    window: int = DEFAULT_GROWING_WINDOW,
) -> int:
    """Of the last ``window`` reports, count week-over-week deltas that grew
    the crowded side. For ``crowded_long`` this means delta > 0; for
    ``crowded_short`` delta < 0; for ``neutral`` returns 0.
    """
    if side == "neutral" or len(nets) < window + 1:
        return 0
    recent = nets[-(window + 1):]
    deltas = [recent[i + 1] - recent[i] for i in range(window)]
    if side == "crowded_long":
        return sum(1 for d in deltas if d > 0)
    return sum(1 for d in deltas if d < 0)


def _flip_tag(tag: CrowdednessTag) -> CrowdednessTag:
    if tag == "crowded_long":
        return "crowded_short"
    if tag == "crowded_short":
        return "crowded_long"
    return "neutral"


def compute_crowdedness(
    symbol: str,
    entries: list[CotEntry],
    *,
    lookback_weeks: int = DEFAULT_LOOKBACK_WEEKS,
    long_threshold: Decimal = DEFAULT_LONG_THRESHOLD,
    short_threshold: Decimal = DEFAULT_SHORT_THRESHOLD,
    growing_window: int = DEFAULT_GROWING_WINDOW,
) -> Crowdedness:
    """Score the latest weekly entry against the trailing ``lookback_weeks``
    distribution of managed-money net positioning.

    Raises ``ValueError`` if the symbol isn't mapped or the series is empty.
    """
    if symbol not in SYMBOL_TO_CFTC:
        raise ValueError(f"{symbol} has no CFTC mapping (see SYMBOL_TO_CFTC)")
    if not entries:
        raise ValueError(f"{symbol}: no COT entries supplied")

    contract = SYMBOL_TO_CFTC[symbol]
    sorted_entries = sorted(entries, key=lambda e: e.report_date)
    window = sorted_entries[-lookback_weeks:]
    nets = [e.mm_net for e in window]
    latest = window[-1]

    pct = percentile_rank(latest.mm_net, nets)
    contract_tag = tag_from_percentile(
        pct, long_threshold=long_threshold, short_threshold=short_threshold,
    )
    weeks_growing = _count_growing(nets, side=contract_tag, window=growing_window)

    inverse = symbol in INVERSE_SYMBOLS
    symbol_tag: CrowdednessTag = _flip_tag(contract_tag) if inverse else contract_tag

    return Crowdedness(
        symbol=symbol,
        contract_code=contract.code,
        contract_label=contract.label,
        as_of=latest.report_date,
        latest_net=latest.mm_net,
        percentile=pct,
        tag=symbol_tag,
        weeks_growing=weeks_growing,
        lookback_weeks=len(window),
        inverse=inverse,
    )


# ---------- Cache ----------------------------------------------------------


def cache_path(symbol: str, *, cache_dir: Path = DEFAULT_CACHE_DIR) -> Path:
    return cache_dir / f"{symbol}.json"


def save_cache(
    symbol: str,
    entries: list[CotEntry],
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
                "report_date": e.report_date.isoformat(),
                "contract_code": e.contract_code,
                "mm_long": str(e.mm_long),
                "mm_short": str(e.mm_short),
            }
            for e in sorted(entries, key=lambda x: x.report_date)
        ],
    }
    path.write_text(json.dumps(payload, indent=2))
    return path


def load_cache(
    symbol: str,
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> list[CotEntry]:
    path = cache_path(symbol, cache_dir=cache_dir)
    if not path.exists():
        return []
    payload = json.loads(path.read_text())
    return [
        CotEntry(
            report_date=datetime.fromisoformat(r["report_date"]),
            contract_code=str(r["contract_code"]),
            mm_long=D(r["mm_long"]),
            mm_short=D(r["mm_short"]),
        )
        for r in payload.get("entries", [])
    ]


# ---------- Live fetcher (CFTC Socrata API) --------------------------------


def fetch_socrata(
    contract_code: str,
    *,
    dataset: DatasetKind = "disagg",
    weeks: int = DEFAULT_LOOKBACK_WEEKS,
    endpoint: Optional[str] = None,
    timeout: float = 20.0,
) -> list[CotEntry]:
    """Pull weekly COT entries for one contract from CFTC.

    Routes to the right Socrata endpoint based on ``dataset`` (Disaggregated
    for commodities, Legacy for FX + indices). Caller can override
    ``endpoint`` for tests/mirrors. Returns oldest-first.

    No auth required.
    """
    import httpx  # lazy import — only fetcher needs it

    url = endpoint or _DATASET_CONFIG[dataset]["endpoint"]
    params = {
        "$where": f"cftc_contract_market_code='{contract_code}'",
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": str(weeks),
    }
    resp = httpx.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    rows = resp.json()
    return list(reversed([CotEntry.from_socrata(r, dataset=dataset) for r in rows]))


def refresh_symbol(
    symbol: str,
    *,
    weeks: int = DEFAULT_LOOKBACK_WEEKS,
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> tuple[Path, int]:
    """Fetch + cache one symbol's COT history. Returns (path, n_entries).

    Routes to the correct Socrata dataset based on the symbol's mapping.
    """
    if symbol not in SYMBOL_TO_CFTC:
        raise ValueError(f"{symbol} has no CFTC mapping")
    contract = SYMBOL_TO_CFTC[symbol]
    entries = fetch_socrata(contract.code, dataset=contract.dataset, weeks=weeks)
    path = save_cache(symbol, entries, cache_dir=cache_dir)
    return path, len(entries)


# ---------- Provider protocol (for FXSSI / AV-options extension) -----------


class CrowdednessProvider(Protocol):
    """Pluggable interface for non-COT crowdedness signals.

    Future providers:
    - **FXSSI retail sentiment**: > 75% one-sided retail = contrarian signal.
      Useful for GER40 and crosses where CFTC doesn't apply.
    - **AlphaVantage options expiry / open-interest**: extreme call/put OI
      ratios near expiry = positioning pressure proxy. Useful for SPX500,
      NAS100, single-name equities.

    Implementations should return a ``Crowdedness`` with ``contract_code``
    set to a provider-prefixed key (e.g. ``fxssi:GER40``) so blends can
    distinguish source.
    """

    def get_crowdedness(self, symbol: str) -> Optional[Crowdedness]: ...


__all__ = [
    "CrowdednessTag",
    "DatasetKind",
    "CftcContract",
    "SYMBOL_TO_CFTC",
    "INVERSE_SYMBOLS",
    "CotEntry",
    "Crowdedness",
    "CrowdednessProvider",
    "percentile_rank",
    "tag_from_percentile",
    "compute_crowdedness",
    "cache_path",
    "save_cache",
    "load_cache",
    "fetch_socrata",
    "refresh_symbol",
    "DEFAULT_CACHE_DIR",
    "DEFAULT_LOOKBACK_WEEKS",
    "SOCRATA_ENDPOINT",
    "SOCRATA_DISAGG",
    "SOCRATA_LEGACY",
]
