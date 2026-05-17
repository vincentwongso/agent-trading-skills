"""AlphaVantage options open-interest crowdedness scoring.

Companion to :mod:`trading_agent_skills.cot_crowdedness`. Where COT measures
*positioning magnitude* (managed-money net position vs trailing distribution),
this module measures *positioning flavour* — the put/call open-interest ratio
on listed US-equity options near monthly expiry, percentile-ranked vs a
trailing window of prior ratios.

Both providers emit the same
:class:`trading_agent_skills.cot_crowdedness.Crowdedness` record so a Stage 1
or Stage 2 consumer can blend or fall back between sources.

Design boundaries
-----------------
- **Pure functions** only. No HTTP, no MCP client imports. The agent (Claude
  Code) is expected to fan out ``mcp__alphavantage__HISTORICAL_OPTIONS`` /
  ``REALTIME_OPTIONS`` calls itself, bundle the response as JSON, and pipe
  the bundle to the CLI (see :mod:`trading_agent_skills.cli.options_crowdedness`).
- **Decimal-typed** open-interest and ratios via
  :func:`trading_agent_skills.decimal_io.D`.
- **Tag semantics** (note: opposite-sign vs COT):
    * High put/call OI ratio + high percentile = bearish positioning crowd =
      ``crowded_short`` (mass hedging / outright shorting via puts).
    * Low put/call OI ratio + low percentile = bullish positioning crowd =
      ``crowded_long`` (mass call-buying).
  The Shapiro-style contrarian-fade playbook (see ``strategies/crowded-fade.md``)
  consumes these tags as the *positioning-extreme filter*, not as a trigger.
- **Pin-risk flag** — when the nearest monthly expiry is within
  ``pin_risk_days`` of the snapshot, the returned ``contract_code`` is
  suffixed ``:pin`` so downstream consumers can opt out of fade trades into
  dealer-gamma pinning. Default 7 calendar days.

Useful for ``SPX500``, ``NAS100``, single-name US equities. NOT useful for FX
or non-US futures.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Optional

from trading_agent_skills.decimal_io import D
from trading_agent_skills.cot_crowdedness import (
    Crowdedness,
    CrowdednessProvider,
    CrowdednessTag,
    percentile_rank,
)


# Defaults ------------------------------------------------------------------

DEFAULT_LONG_THRESHOLD = Decimal("90")   # >= 90th pct of put/call ratio history = crowded_short
DEFAULT_SHORT_THRESHOLD = Decimal("10")  # <= 10th pct = crowded_long
DEFAULT_PIN_RISK_DAYS = 7
DEFAULT_GROWING_WINDOW = 4

# Floor to avoid divide-by-zero when call OI is zero / missing.
_RATIO_FLOOR = Decimal("0.0001")


# ---------- Domain types ---------------------------------------------------


@dataclass(frozen=True)
class OptionsChainSnapshot:
    """A single point-in-time options chain summary for one underlier.

    ``nearest_expiry`` is stored as either a ``date`` or an ISO-format string
    (the AV blob shape is usually a string; we keep it permissive so callers
    that already parsed don't have to round-trip).
    """

    as_of: datetime
    symbol: str
    total_call_oi: Decimal
    total_put_oi: Decimal
    nearest_expiry: date | str
    days_to_nearest_expiry: int

    @classmethod
    def from_av_chain(cls, blob: dict[str, Any]) -> "OptionsChainSnapshot":
        """Reduce an AlphaVantage MCP options-chain payload to a snapshot.

        Expected shape (canonical AV ``HISTORICAL_OPTIONS`` /
        ``REALTIME_OPTIONS`` response, bundled by the agent)::

            {
              "symbol": "SPY",
              "as_of": "2026-05-17T16:00:00+00:00",
              "data": [
                {"contract_type": "call", "open_interest": "1000",
                 "expiration": "2026-06-19", ...},
                ...
              ]
            }

        Rows with missing/unparseable ``contract_type``, ``open_interest``,
        or ``expiration`` are silently skipped — the AV feed occasionally
        returns partial rows and we'd rather degrade gracefully than fail
        the whole snapshot.
        """
        symbol = str(blob.get("symbol") or blob.get("ticker") or "")
        as_of = _parse_as_of(blob.get("as_of"))
        rows = blob.get("data") or blob.get("contracts") or []

        total_call = Decimal(0)
        total_put = Decimal(0)
        expirations: list[date] = []

        for row in rows:
            if not isinstance(row, dict):
                continue
            ct_raw = row.get("contract_type") or row.get("type")
            oi_raw = row.get("open_interest")
            exp_raw = row.get("expiration") or row.get("expiry")
            if ct_raw is None or oi_raw is None or exp_raw is None:
                continue
            ct = str(ct_raw).strip().lower()
            if ct not in ("call", "put"):
                continue
            try:
                oi = D(oi_raw)
            except (TypeError, ValueError, ArithmeticError):
                continue
            try:
                exp_date = _parse_date(exp_raw)
            except ValueError:
                continue

            if ct == "call":
                total_call += oi
            else:
                total_put += oi
            expirations.append(exp_date)

        if not expirations:
            # No usable rows — emit a snapshot with zero OI and a far-future
            # placeholder expiry so consumers see "no pin risk" rather than
            # blowing up.
            nearest = as_of.date()
            days = 0
        else:
            ref_date = as_of.date()
            future = sorted(d for d in expirations if d >= ref_date)
            nearest = future[0] if future else max(expirations)
            days = (nearest - ref_date).days

        return cls(
            as_of=as_of,
            symbol=symbol,
            total_call_oi=total_call,
            total_put_oi=total_put,
            nearest_expiry=nearest,
            days_to_nearest_expiry=days,
        )


@dataclass(frozen=True)
class OptionsHistoryEntry:
    """One prior put/call OI ratio observation used to build the baseline."""

    as_of: datetime
    put_call_oi_ratio: Decimal


# ---------- Pure scoring ---------------------------------------------------


def put_call_oi_ratio(snapshot: OptionsChainSnapshot) -> Decimal:
    """Return total_put_oi / total_call_oi. Floors call OI at 0.0001."""
    call = snapshot.total_call_oi if snapshot.total_call_oi > 0 else _RATIO_FLOOR
    return snapshot.total_put_oi / call


def _count_growing(
    ratios: list[Decimal],
    *,
    side: CrowdednessTag,
    window: int = DEFAULT_GROWING_WINDOW,
) -> int:
    """Count week-over-week deltas in the last ``window`` ratios that grew
    the crowded side.

    For ``crowded_short`` (high p/c ratio) we count deltas > 0; for
    ``crowded_long`` (low p/c ratio) we count deltas < 0. ``neutral``
    returns 0.
    """
    if side == "neutral" or len(ratios) < window + 1:
        return 0
    recent = ratios[-(window + 1):]
    deltas = [recent[i + 1] - recent[i] for i in range(window)]
    if side == "crowded_short":
        return sum(1 for d in deltas if d > 0)
    return sum(1 for d in deltas if d < 0)


def compute_crowdedness(
    symbol: str,
    snapshot: OptionsChainSnapshot,
    history: list[OptionsHistoryEntry],
    *,
    long_threshold: Decimal = DEFAULT_LONG_THRESHOLD,
    short_threshold: Decimal = DEFAULT_SHORT_THRESHOLD,
    pin_risk_days: int = DEFAULT_PIN_RISK_DAYS,
    growing_window: int = DEFAULT_GROWING_WINDOW,
) -> Crowdedness:
    """Score the current snapshot against a trailing distribution of
    put/call OI ratios.

    The ``history`` distribution should be the per-day (or per-week) put/call
    ratio for the same symbol over a comparable lookback (e.g. ~90 trading
    days). The current snapshot's ratio is appended to the distribution
    before percentile-ranking so a fresh extreme is always at 100 (or 0).

    Returns a :class:`Crowdedness` with provider-prefixed ``contract_code``
    (``avopt:<symbol>``, optionally ``:pin``).
    """
    ratio = put_call_oi_ratio(snapshot)

    distribution: list[Decimal] = [e.put_call_oi_ratio for e in history]
    distribution.append(ratio)
    pct = percentile_rank(ratio, distribution)

    # Note: high p/c ratio at high percentile = crowded_short (puts dominant).
    if pct >= long_threshold:
        tag: CrowdednessTag = "crowded_short"
    elif pct <= short_threshold:
        tag = "crowded_long"
    else:
        tag = "neutral"

    # weeks_growing counts the last `growing_window` history deltas trending
    # toward the crowded side. We use history-only (not history+current) so
    # a single fresh spike doesn't trivially satisfy the filter.
    historical_ratios = [e.put_call_oi_ratio for e in history]
    weeks_growing = _count_growing(historical_ratios, side=tag, window=growing_window)

    pin = snapshot.days_to_nearest_expiry <= pin_risk_days
    contract_code = f"avopt:{symbol}:pin" if pin else f"avopt:{symbol}"

    return Crowdedness(
        symbol=symbol,
        contract_code=contract_code,
        contract_label="OPTIONS OI",
        as_of=snapshot.as_of,
        latest_net=ratio,
        percentile=pct,
        tag=tag,
        weeks_growing=weeks_growing,
        lookback_weeks=len(history),
        inverse=False,
    )


# ---------- Provider --------------------------------------------------------


class AlphaVantageOptionsProvider:
    """:class:`CrowdednessProvider` adapter backed by two caller-supplied
    fetch callbacks.

    The package boundary forbids HTTP / MCP client imports, so the harness
    (Claude Code agent or a wrapper) is expected to inject:

    - ``snapshot_fn(symbol) -> Optional[OptionsChainSnapshot]`` — returns the
      current chain snapshot, or ``None`` if no data is available (e.g. AV
      MCP server is offline or the symbol isn't listed).
    - ``history_fn(symbol) -> list[OptionsHistoryEntry]`` — returns the
      trailing window of put/call ratios. May return an empty list; in that
      case ``compute_crowdedness`` will still rank the current ratio at 100%
      against a single-element distribution.

    Callers may pass ``snapshot_fn=None`` in test scenarios to assert the
    no-data branch returns ``None`` without invoking ``history_fn``.
    """

    def __init__(
        self,
        snapshot_fn: Optional[Callable[[str], Optional[OptionsChainSnapshot]]],
        history_fn: Callable[[str], list[OptionsHistoryEntry]],
        *,
        long_threshold: Decimal = DEFAULT_LONG_THRESHOLD,
        short_threshold: Decimal = DEFAULT_SHORT_THRESHOLD,
        pin_risk_days: int = DEFAULT_PIN_RISK_DAYS,
    ) -> None:
        self._snapshot_fn = snapshot_fn
        self._history_fn = history_fn
        self._long_threshold = long_threshold
        self._short_threshold = short_threshold
        self._pin_risk_days = pin_risk_days

    def get_crowdedness(self, symbol: str) -> Optional[Crowdedness]:
        if self._snapshot_fn is None:
            return None
        snapshot = self._snapshot_fn(symbol)
        if snapshot is None:
            return None
        history = self._history_fn(symbol) or []
        return compute_crowdedness(
            symbol,
            snapshot,
            history,
            long_threshold=self._long_threshold,
            short_threshold=self._short_threshold,
            pin_risk_days=self._pin_risk_days,
        )


# ---------- Internal helpers ----------------------------------------------


def _parse_as_of(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif value is None:
        dt = datetime.now(timezone.utc)
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    s = str(value).strip()
    # Tolerate "YYYY-MM-DD" and ISO datetime forms.
    if "T" in s:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    return date.fromisoformat(s)


# Statically declare the CrowdednessProvider implementation for type checkers.
_provider_check: type[CrowdednessProvider] = AlphaVantageOptionsProvider  # noqa: F841


__all__ = [
    "OptionsChainSnapshot",
    "OptionsHistoryEntry",
    "AlphaVantageOptionsProvider",
    "put_call_oi_ratio",
    "compute_crowdedness",
    "percentile_rank",
    "DEFAULT_LONG_THRESHOLD",
    "DEFAULT_SHORT_THRESHOLD",
    "DEFAULT_PIN_RISK_DAYS",
    "DEFAULT_GROWING_WINDOW",
]
