import json
import subprocess
import sys
from pathlib import Path

import pytest

from trading_agent_skills.account_paths import resolve_account_paths


_VALID_CHARTER_TEXT = """\
mode: demo
account_id: 12345678
heartbeat: 1h
hard_caps:
  per_trade_risk_pct: 1.0
  daily_loss_pct: 5.0
  max_concurrent_positions: 3
charter_version: 1
created_at: 2026-04-30T14:00:00+10:00
created_account_balance: 10000.00
trading_style: day
sessions_allowed: []
instruments: []
allowed_setups: []
notes: ""
"""


def test_cli_emits_proposal(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    paths = resolve_account_paths(account_id="12345678", base=tmp_path / ".trading-agent-skills")
    paths.ensure_dirs()
    paths.charter.write_text(_VALID_CHARTER_TEXT, encoding="utf-8")
    paths.journal.touch()

    res = subprocess.run(
        [sys.executable, "-m", "trading_agent_skills.cli.strategy_review",
         "propose", "--account-id", "12345678",
         "--since", "2026-04-25T00:00:00Z",
         "--until", "2026-05-02T00:00:00Z"],
        text=True, capture_output=True,
    )
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["status"] == "ok"
    proposal_path = Path(out["proposal_path"])
    assert proposal_path.is_file()
    assert "Strategy review" in proposal_path.read_text(encoding="utf-8")


def test_cli_apply_changes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    paths = resolve_account_paths(account_id="12345678", base=tmp_path / ".trading-agent-skills")
    paths.ensure_dirs()
    paths.charter.write_text(_VALID_CHARTER_TEXT, encoding="utf-8")

    res = subprocess.run(
        [sys.executable, "-m", "trading_agent_skills.cli.strategy_review",
         "apply", "--account-id", "12345678"],
        input=json.dumps({"per_trade_risk_pct": 0.8}),
        text=True, capture_output=True,
    )
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["status"] == "ok"
    assert out["new_version"] == 2
    assert "per_trade_risk_pct: 0.8" in paths.charter.read_text(encoding="utf-8")


def test_cli_apply_rejects_locked_field(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    paths = resolve_account_paths(account_id="12345678", base=tmp_path / ".trading-agent-skills")
    paths.ensure_dirs()
    paths.charter.write_text(_VALID_CHARTER_TEXT, encoding="utf-8")

    res = subprocess.run(
        [sys.executable, "-m", "trading_agent_skills.cli.strategy_review",
         "apply", "--account-id", "12345678"],
        input=json.dumps({"mode": "live"}),
        text=True, capture_output=True,
    )
    assert res.returncode != 0
    assert "locked" in (res.stderr + res.stdout)
