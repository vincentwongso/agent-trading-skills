from pathlib import Path

import pytest

from trading_agent_skills.account_paths import AccountPaths, resolve_account_paths


def test_resolve_paths_returns_namespaced_dirs(tmp_path: Path) -> None:
    paths = resolve_account_paths(account_id="12345678", base=tmp_path)
    assert paths.root == tmp_path / "accounts" / "12345678"
    assert paths.charter == paths.root / "charter.md"
    assert paths.charter_versions == paths.root / "charter_versions"
    assert paths.journal == paths.root / "journal.jsonl"
    assert paths.decisions == paths.root / "decisions.jsonl"
    assert paths.proposals == paths.root / "proposals"
    assert paths.daily_state == paths.root / "daily_state.json"


def test_resolve_paths_rejects_blank_account_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="account_id"):
        resolve_account_paths(account_id="", base=tmp_path)


def test_resolve_paths_rejects_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="account_id"):
        resolve_account_paths(account_id="../etc", base=tmp_path)


def test_ensure_dirs_creates_root_versions_proposals(tmp_path: Path) -> None:
    paths = resolve_account_paths(account_id="12345678", base=tmp_path)
    paths.ensure_dirs()
    assert paths.root.is_dir()
    assert paths.charter_versions.is_dir()
    assert paths.proposals.is_dir()


def test_default_base_is_trading_agent_skills_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    paths = resolve_account_paths(account_id="12345678")
    assert paths.root == Path.home() / ".trading-agent-skills" / "accounts" / "12345678"
