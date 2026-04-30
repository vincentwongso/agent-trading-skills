#!/usr/bin/env python
"""Thin shim — kept for parity with the other skills' scripts/ directories."""

from __future__ import annotations

import sys

from trading_agent_skills.cli.price_action import main


if __name__ == "__main__":
    sys.exit(main())
