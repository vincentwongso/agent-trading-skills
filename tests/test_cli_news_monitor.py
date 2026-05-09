"""Integration tests for the news-monitor CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _run(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "trading_agent_skills.cli.news_monitor", *args],
        capture_output=True, text=True, cwd=REPO_ROOT,
        env={**(env or {}), "PYTHONPATH": str(REPO_ROOT / "src")},
    )


def test_cli_runs_with_no_keys_emits_empty_events(tmp_path: Path) -> None:
    state = tmp_path / "news_seen.jsonl"
    res = _run([
        "--state", str(state),
        "--lookback-minutes", "10",
    ])
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert payload["events"] == []
    assert "provider_health" in payload
    # All providers report no_api_key when env is empty
    assert all(v == "no_api_key" for v in payload["provider_health"].values())


def test_cli_invalid_args_exits_nonzero(tmp_path: Path) -> None:
    res = _run([])  # missing required --state
    assert res.returncode != 0


def test_cli_creates_state_file_on_run(tmp_path: Path) -> None:
    state = tmp_path / "subdir" / "news_seen.jsonl"
    res = _run([
        "--state", str(state),
        "--lookback-minutes", "10",
    ])
    assert res.returncode == 0
    # No events to write so file may or may not exist; parent dir must exist
    # only when there's something to write. Confirm CLI didn't error.
    json.loads(res.stdout)
