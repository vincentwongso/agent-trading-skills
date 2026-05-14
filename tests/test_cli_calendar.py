"""End-to-end CLI tests for trading-agent-skills-calendar.

Uses ``httpx.MockTransport`` to stub Calix responses without network.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from trading_agent_skills.cli.calendar import build_parser, run


def _stub_client_factory(handler):
    """Returns a ``client_factory`` callable matching ``run()``'s signature."""
    transport = httpx.MockTransport(handler)

    def factory(cache_dir: Path):
        from trading_agent_skills.calix_client import CalixClient
        return CalixClient(cache_dir=cache_dir, transport=transport)

    return factory


def test_parser_rejects_no_subcommand(capsys) -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_parser_rejects_economic_with_no_verb(capsys) -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["economic"])
