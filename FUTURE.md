<!-- FUTURE.md -->
# Future TODOs (out of scope for current shipped work)

## mt5-mcp backtest support

**Why:** Currently the autonomous-trading-loop has no simulation path —
agent learns entirely on live demo data. Adding MT5 strategy-tester
support to mt5-mcp would unlock:

- Validating strategy-review proposals against historical data before
  applying them to charter.
- Backtesting newly-allowed setup labels before they go live.
- Onboarding new instruments without spending a week on demo ticks.

**Where:** Upstream change in `https://github.com/vincentwongso/mt5-mcp`.
MT5 exposes the strategy tester via the IDE; programmatic access requires
the `MqlTester` API or the recently-added Python `MetaTrader5.copy_rates_*`
combined with manual entry/exit simulation.

**Priority:** Low. Safe to operate without it on a demo account.

## Correlation matrix for exposure-overlap

The pre-trade-checklist exposure-overlap heuristic is shared-currency-only
(EURUSD long + EURGBP short → flagged). Real correlation between e.g.
USOIL/UKOIL/NAS100 (broad-risk-on) is not captured. A small CSV-driven
correlation matrix would tighten the heuristic.

## Sentiment classification on news articles

session-news-brief currently uses keyword-driven impact only. A small
classifier (or an LLM call) on article body could meaningfully improve
swing-candidate quality.

## Multi-account simultaneous trading

Out of scope by design. v1 supports one charter per session; switching
accounts is a clean-slate operation. Multi-account would require a
concurrency model (which heartbeat fires when two are due simultaneously).

## Auto-pause on extended drawdown

Charter could grow `pause_after_consecutive_losses: N` in v2. Avoiding
in v1 to keep the install Q&A short.
