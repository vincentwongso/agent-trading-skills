#!/usr/bin/env python3
"""Thin shim that delegates to ``trading_agent_skills.cli.options_crowdedness``."""

from trading_agent_skills.cli.options_crowdedness import main

if __name__ == "__main__":
    raise SystemExit(main())
