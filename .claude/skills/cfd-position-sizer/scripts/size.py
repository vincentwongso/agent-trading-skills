#!/usr/bin/env python3
"""Thin shim that delegates to ``cfd_skills.cli.size``.

The skill could call ``python -m cfd_skills.cli.size`` directly, but a
shim makes the bash command in SKILL.md easier to read and gives us a
single line to update if the entry point ever moves.
"""

from cfd_skills.cli.size import main

if __name__ == "__main__":
    raise SystemExit(main())
