#!/usr/bin/env python3
"""Thin shim that delegates to ``trading_agent_skills.cli.retail_sentiment``."""

from trading_agent_skills.cli.retail_sentiment import main

if __name__ == "__main__":
    raise SystemExit(main())
