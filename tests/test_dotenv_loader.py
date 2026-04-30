"""dotenv_loader: tiny zero-dep .env reader for the news CLI."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from trading_agent_skills.dotenv_loader import load_env_file


@pytest.fixture
def tmp_env(tmp_path: Path) -> Path:
    return tmp_path / ".env"


@pytest.fixture(autouse=True)
def _clean_env():
    # Save + clear env keys before each test, restore originals after.
    # We can't rely on monkeypatch alone because the loader mutates os.environ
    # via setdefault — those mutations bypass monkeypatch's restore tracking.
    keys = ("FINNHUB_API_KEY", "MARKETAUX_API_KEY", "FOREXNEWS_API_KEY", "DOTENV_TEST_X")
    saved = {k: os.environ.pop(k, None) for k in keys}
    yield
    for k in keys:
        os.environ.pop(k, None)
        if saved[k] is not None:
            os.environ[k] = saved[k]


class TestLoadEnvFile:
    def test_missing_file_is_noop(self, tmp_path: Path):
        loaded = load_env_file(tmp_path / "does-not-exist.env")
        assert loaded == {}
        assert "FINNHUB_API_KEY" not in os.environ

    def test_parses_simple_key_value(self, tmp_env: Path):
        tmp_env.write_text("FINNHUB_API_KEY=abc123\n", encoding="utf-8")
        loaded = load_env_file(tmp_env)
        assert loaded == {"FINNHUB_API_KEY": "abc123"}
        assert os.environ["FINNHUB_API_KEY"] == "abc123"

    def test_ignores_blank_lines_and_comments(self, tmp_env: Path):
        tmp_env.write_text(
            "\n# leading comment\nFINNHUB_API_KEY=abc\n\n  # indented comment\nMARKETAUX_API_KEY=def\n",
            encoding="utf-8",
        )
        loaded = load_env_file(tmp_env)
        assert loaded == {"FINNHUB_API_KEY": "abc", "MARKETAUX_API_KEY": "def"}

    def test_strips_double_quotes(self, tmp_env: Path):
        tmp_env.write_text('FINNHUB_API_KEY="quoted value"\n', encoding="utf-8")
        load_env_file(tmp_env)
        assert os.environ["FINNHUB_API_KEY"] == "quoted value"

    def test_strips_single_quotes(self, tmp_env: Path):
        tmp_env.write_text("FINNHUB_API_KEY='single quoted'\n", encoding="utf-8")
        load_env_file(tmp_env)
        assert os.environ["FINNHUB_API_KEY"] == "single quoted"

    def test_real_env_wins(self, tmp_env: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("FINNHUB_API_KEY", "from-shell")
        tmp_env.write_text("FINNHUB_API_KEY=from-file\n", encoding="utf-8")
        loaded = load_env_file(tmp_env)
        # Returned dict reflects what was *parsed* from the file...
        assert loaded == {"FINNHUB_API_KEY": "from-file"}
        # ...but real env wins.
        assert os.environ["FINNHUB_API_KEY"] == "from-shell"

    def test_skips_lines_without_equals(self, tmp_env: Path):
        tmp_env.write_text(
            "this is not a key=value line\nFINNHUB_API_KEY=ok\n",
            encoding="utf-8",
        )
        loaded = load_env_file(tmp_env)
        # The malformed line, when split on '=', produced a single token: skip.
        assert loaded == {"FINNHUB_API_KEY": "ok"}

    def test_strips_export_prefix(self, tmp_env: Path):
        # Bash users sometimes paste `export FOO=bar` from their shells.
        tmp_env.write_text("export FINNHUB_API_KEY=abc\n", encoding="utf-8")
        load_env_file(tmp_env)
        assert os.environ["FINNHUB_API_KEY"] == "abc"

    def test_ignores_empty_key(self, tmp_env: Path):
        tmp_env.write_text("=novalue\nDOTENV_TEST_X=ok\n", encoding="utf-8")
        loaded = load_env_file(tmp_env)
        assert loaded == {"DOTENV_TEST_X": "ok"}

    def test_value_with_equals_signs(self, tmp_env: Path):
        # Some API keys / URLs contain `=`. Only split on the first one.
        tmp_env.write_text("DOTENV_TEST_X=a=b=c\n", encoding="utf-8")
        load_env_file(tmp_env)
        assert os.environ["DOTENV_TEST_X"] == "a=b=c"
