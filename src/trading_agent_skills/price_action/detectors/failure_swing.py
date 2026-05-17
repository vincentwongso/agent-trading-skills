"""Detector: failure-swing reversal patterns at a recent extreme.

Three variants — all signal that a directional thrust has just stalled, which
is the price-trigger half of contrarian playbooks (e.g. Shapiro-style fades
combined with COT crowdedness from ``cot_crowdedness``).

Pure pattern functions (``is_failure_swing``, ``is_outside_day_reversal``,
``is_three_bar_reversal``) operate on a list of recent bars (oldest first)
and return a Side or ``None``. ``detect`` wraps them into ``CandidateSetup``
records on the highest-TF context (D1 in swing mode).

These detectors do NOT require S/R proximity — failure swings are about
extremes themselves, not about confluence with prior structure.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal, Optional

from trading_agent_skills.indicators import Bar
from trading_agent_skills.price_action.context import ScanContext
from trading_agent_skills.price_action.detectors import CandidateSetup, EntryZone


Side = Literal["long", "short"]

_LOOKBACK_EXTREME = 5     # window for "new N-day extreme"
_FAILURE_WINDOW = 3       # bars after the extreme within which failure must print
_DEFAULT_INVALIDATION_BUFFER_TICKS = 10


def _body_midpoint(bar: Bar) -> Decimal:
    return (bar.open + bar.close) / Decimal(2)


def is_failure_swing(
    bars: list[Bar],
    *,
    lookback: int = _LOOKBACK_EXTREME,
    window: int = _FAILURE_WINDOW,
) -> Optional[Side]:
    """Detect a failure swing on the most recent bar.

    Bullish-side failure (returns ``"short"``):
      Within the last ``window`` bars, price made a new ``lookback``-bar HIGH,
      and the most recent close is now BELOW the prior swing high (the
      ``lookback``-bar high *before* the new extreme).

    Bearish-side failure (returns ``"long"``):
      Mirror — new ``lookback``-bar LOW, then close above prior swing low.
    """
    needed = lookback + window
    if len(bars) < needed:
        return None
    last = bars[-1]

    recent = bars[-(window + 1):]
    prior = bars[-(needed):-(window + 1)]
    if not prior:
        return None

    prior_high = max(b.high for b in prior)
    prior_low = min(b.low for b in prior)

    pushed_new_high = any(b.high > prior_high for b in recent[:-1] + [last])
    pushed_new_low = any(b.low < prior_low for b in recent[:-1] + [last])

    if pushed_new_high and last.close < prior_high:
        return "short"
    if pushed_new_low and last.close > prior_low:
        return "long"
    return None


def is_outside_day_reversal(
    bars: list[Bar],
    *,
    lookback: int = _LOOKBACK_EXTREME,
) -> Optional[Side]:
    """The most recent bar took out the prior ``lookback``-bar extreme AND
    closed on the opposite side of the prior bar's close.

    Returns ``"short"`` for a bearish reversal (new high, then closes below
    prior close); ``"long"`` for the mirror.
    """
    if len(bars) < lookback + 2:
        return None
    last = bars[-1]
    prev = bars[-2]
    prior = bars[-(lookback + 2):-2]
    if not prior:
        return None

    prior_high = max(b.high for b in prior)
    prior_low = min(b.low for b in prior)

    if last.high > prior_high and last.close < prev.close:
        return "short"
    if last.low < prior_low and last.close > prev.close:
        return "long"
    return None


def is_three_bar_reversal(bars: list[Bar]) -> Optional[Side]:
    """Three consecutive closes in the reversal direction, each past the prior
    bar's body midpoint.

    Bearish reversal (returns ``"short"``):
      close[-3] > close[-4],  bars[-2..0] each close below the prior bar's
      body midpoint.

    Bullish reversal (returns ``"long"``): mirror.
    """
    if len(bars) < 4:
        return None
    pivot, b1, b2, b3 = bars[-4], bars[-3], bars[-2], bars[-1]
    # Pivot direction = first bar of the "thrust" we are reversing.
    bearish = (
        b1.close < _body_midpoint(pivot)
        and b2.close < _body_midpoint(b1)
        and b3.close < _body_midpoint(b2)
    )
    bullish = (
        b1.close > _body_midpoint(pivot)
        and b2.close > _body_midpoint(b1)
        and b3.close > _body_midpoint(b2)
    )
    if bearish and not bullish:
        return "short"
    if bullish and not bearish:
        return "long"
    return None


def detect(ctx: ScanContext) -> list[CandidateSetup]:
    """Run all three failure-pattern checks on the highest-TF context.

    Used by contrarian playbooks. Emits at most one candidate per detected
    pattern; if multiple patterns fire on the same bar, all are returned and
    downstream ranking picks.
    """
    tfs = list(ctx.tfs.keys())
    if not tfs:
        return []
    setup_tf = tfs[-1]      # highest TF (D1 in swing mode)
    trig_tf = tfs[0]
    sctx = ctx.tfs[setup_tf]
    bars = sctx.bars
    needed = _LOOKBACK_EXTREME + _FAILURE_WINDOW
    if len(bars) < needed:
        return []
    last = bars[-1]
    buffer = ctx.tick_size * Decimal(_DEFAULT_INVALIDATION_BUFFER_TICKS)

    out: list[CandidateSetup] = []

    patterns: list[tuple[str, Optional[Side]]] = [
        ("failure_swing", is_failure_swing(bars)),
        ("outside_day_reversal", is_outside_day_reversal(bars)),
        ("three_bar_reversal", is_three_bar_reversal(bars)),
    ]

    # The extreme that the pattern is fading — for stop placement.
    recent_window = bars[-(needed):]
    recent_high = max(b.high for b in recent_window)
    recent_low = min(b.low for b in recent_window)

    for pattern_name, side in patterns:
        if side is None:
            continue
        if side == "short":
            invalidation = recent_high + buffer
            entry_zone = EntryZone(low=last.close, high=last.high)
        else:
            invalidation = recent_low - buffer
            entry_zone = EntryZone(low=last.low, high=last.close)

        out.append(CandidateSetup(
            type="failure_swing",
            tf_setup=setup_tf,
            tf_trigger=trig_tf,
            side=side,
            entry_zone=entry_zone,
            suggested_entry=last.close,
            invalidation=invalidation,
            targets=(),
            confluence=(f"{setup_tf}_{pattern_name}",),
            candle_quality=Decimal("0.7"),
            narrative_hint=(
                f"{pattern_name.replace('_', ' ').title()} "
                f"({side}) on {setup_tf} after {needed}-bar extreme"
            ),
        ))

    return out


__all__ = [
    "detect",
    "is_failure_swing",
    "is_outside_day_reversal",
    "is_three_bar_reversal",
    "Side",
]
