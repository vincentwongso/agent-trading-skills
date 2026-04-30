#!/usr/bin/env python3
"""Thin shim that delegates to ``trading_agent_skills.cli.journal``.

The skill could call ``python -m trading_agent_skills.cli.journal`` directly; this
shim gives the SKILL.md a single short command to reference.
"""

from trading_agent_skills.cli.journal import main

if __name__ == "__main__":
    raise SystemExit(main())
