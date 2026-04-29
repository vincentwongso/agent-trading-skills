"""CLI wrapper for ``cfd_skills.checklist.assess``.

Reads a JSON request bundle from stdin (or ``--input <file>``), runs the
guardian to compute today's risk state, then runs the pre-trade checklist
against that result. Writes a JSON ``ChecklistResult`` to stdout (with a
``guardian`` sub-document attached for the agent's render).

Bundle shape:

    {
      "now_utc": "2026-04-29T21:00:00+00:00",   # optional
      "account": <get_account_info output>,
      "positions": [...],                         # see cli.guardian
      "realized_pnl_today": "0.00",
      "target": {
        "symbol": "XAUUSD",
        "side": "long" | "short",
        "candidate_risk_pct": "1.0"            # optional
      },
      "symbol_context": {
        "currency_base": "XAU",
        "currency_profit": "USD",
        "category": "metals" | "forex" | "indices" | "crypto" | ...,
        "market_open": true
      },
      "calix": {
        "economic_events": [<calix event blob>, ...],
        "earnings_entries": [<calix earnings blob>, ...],
        "economic_stale": false,
        "earnings_stale": false
      },
      "spread": {"current_pts": "12"},          # optional; baseline updated on disk
      "config_path": "/.../config.toml",        # optional override
      "state_path": "/.../daily_state.json",    # optional override
      "spread_baseline_path": "/.../spread_baseline.json"  # optional override
    }

Exit codes: 0 success, 1 schema error, 2 logic error.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from cfd_skills.checklist import (
    CalixEarningsEntry,
    CalixEconomicEvent,
    ChecklistInput,
    SymbolContext,
    assess as checklist_assess,
)
from cfd_skills.config_io import DEFAULT_CONFIG_PATH, load_config
from cfd_skills.daily_state import DEFAULT_STATE_PATH, tick
from cfd_skills.decimal_io import D
from cfd_skills.guardian import (
    AccountSnapshot,
    GuardianInput,
    assess as guardian_assess,
)
from cfd_skills.risk_state import Position
from cfd_skills.spread_baseline import (
    DEFAULT_BASELINE_PATH,
    BaselineStore,
)


def _to_jsonable(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return format(obj, "f")
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    if is_dataclass(obj) and not isinstance(obj, type):
        return _to_jsonable(asdict(obj))
    return obj


def _parse_now(blob: dict[str, Any]) -> datetime:
    raw = blob.get("now_utc")
    if raw is None:
        return datetime.now(timezone.utc)
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _build_positions(entries: list[dict[str, Any]]) -> list[Position]:
    out: list[Position] = []
    for entry in entries:
        out.append(
            Position.from_mcp(
                position=entry["position"],
                symbol=entry["symbol"],
                classification=entry.get("classification"),
                classification_reason=str(entry.get("classification_reason", "")),
            )
        )
    return out


def _build_symbol_ctx(blob: dict[str, Any], symbol: str) -> SymbolContext:
    return SymbolContext(
        symbol=symbol,
        currency_base=str(blob.get("currency_base", "")),
        currency_profit=str(blob.get("currency_profit", "")),
        category=str(blob.get("category", "")),
        market_open=bool(blob.get("market_open", False)),
    )


def _opt_d(v: Any) -> Optional[Decimal]:
    return D(v) if v is not None else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cfd-skills-checklist")
    parser.add_argument(
        "--input", "-i", default="-",
        help="Path to JSON input file ('-' for stdin; default: -).",
    )
    args = parser.parse_args(argv)

    raw = sys.stdin.read() if args.input == "-" else open(args.input, encoding="utf-8").read()
    try:
        bundle = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"ERROR: invalid JSON on stdin: {exc}", file=sys.stderr)
        return 1

    try:
        config_path = Path(bundle.get("config_path", DEFAULT_CONFIG_PATH))
        state_path = Path(bundle.get("state_path", DEFAULT_STATE_PATH))
        baseline_path = Path(
            bundle.get("spread_baseline_path", DEFAULT_BASELINE_PATH)
        )
        config = load_config(config_path, write_default_if_missing=True)
        now_utc = _parse_now(bundle)
        account = AccountSnapshot.from_mcp(bundle["account"])
        positions = _build_positions(bundle.get("positions", []))
        realized = D(bundle.get("realized_pnl_today", "0"))

        target = bundle["target"]
        target_symbol = str(target["symbol"])
        target_side = str(target["side"]).lower()
        if target_side not in ("long", "short"):
            raise ValueError(f"target.side must be long/short, got {target_side!r}")
        candidate_risk_pct = _opt_d(target.get("candidate_risk_pct"))

        sym_ctx_blob = bundle.get("symbol_context", {})
        sym_ctx = _build_symbol_ctx(sym_ctx_blob, target_symbol)

        calix_blob = bundle.get("calix", {})
        economic_events = [
            CalixEconomicEvent.from_blob(b)
            for b in calix_blob.get("economic_events", [])
        ]
        earnings_entries = [
            CalixEarningsEntry.from_blob(b)
            for b in calix_blob.get("earnings_entries", [])
        ]
        economic_stale = bool(calix_blob.get("economic_stale", False))
        earnings_stale = bool(calix_blob.get("earnings_stale", False))

        spread_blob = bundle.get("spread") or {}
        current_spread_pts = _opt_d(spread_blob.get("current_pts"))
    except (KeyError, TypeError, ValueError) as exc:
        print(f"ERROR: malformed input bundle: {exc}", file=sys.stderr)
        return 1

    # 1. Reset bookkeeping + guardian.
    session = tick(
        now_utc=now_utc,
        current_equity=account.equity,
        reset_tz=config.session.reset_tz,
        reset_time=config.session.reset_time,
        path=state_path,
    )
    guardian_input = GuardianInput(
        now_utc=now_utc,
        account=account,
        session_open_balance=session.state.session_open_balance,
        last_reset_utc=session.state.last_reset_utc,
        next_reset_utc=session.next_reset_utc,
        realized_pnl_today=realized,
        positions=positions,
        config=config.risk,
    )
    try:
        guardian_result = guardian_assess(guardian_input)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    # 2. Spread baseline read (use prior baseline for the WARN), then update.
    baseline_store = BaselineStore.load(baseline_path)
    prior_baseline = baseline_store.get(target_symbol)

    # 3. Checklist.
    checklist_input = ChecklistInput(
        symbol_ctx=sym_ctx,
        side=target_side,  # type: ignore[arg-type]
        candidate_risk_pct=candidate_risk_pct,
        guardian=guardian_result,
        economic_events=economic_events,
        earnings_entries=earnings_entries,
        economic_stale=economic_stale,
        earnings_stale=earnings_stale,
        existing_positions=positions,
        current_spread_pts=current_spread_pts,
        spread_baseline=prior_baseline,
        now_utc=now_utc,
        config=config.risk,
    )
    try:
        checklist_result = checklist_assess(checklist_input)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    # 4. Update spread baseline AFTER the check (so WARN reflects pre-update state).
    if current_spread_pts is not None:
        baseline_store.update(target_symbol, current_spread_pts, now_utc=now_utc)
        baseline_store.save(baseline_path)

    # 5. Output: checklist verdict + nested guardian for the agent's render.
    output = _to_jsonable(checklist_result)
    output["guardian"] = _to_jsonable(guardian_result)
    output["session_just_reset"] = session.just_reset
    json.dump(output, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
