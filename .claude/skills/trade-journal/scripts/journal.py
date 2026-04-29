#!/usr/bin/env python3
"""Thin shim that delegates to ``cfd_skills.cli.journal``.

The skill could call ``python -m cfd_skills.cli.journal`` directly; this
shim gives the SKILL.md a single short command to reference.
"""

from cfd_skills.cli.journal import main

if __name__ == "__main__":
    raise SystemExit(main())
