#!/usr/bin/env python3
"""Thin shim that delegates to ``cfd_skills.cli.checklist``."""

from cfd_skills.cli.checklist import main

if __name__ == "__main__":
    raise SystemExit(main())
