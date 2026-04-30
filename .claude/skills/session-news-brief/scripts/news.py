#!/usr/bin/env python3
"""Thin shim that delegates to ``trading_agent_skills.cli.news``."""

from trading_agent_skills.cli.news import main

if __name__ == "__main__":
    raise SystemExit(main())
