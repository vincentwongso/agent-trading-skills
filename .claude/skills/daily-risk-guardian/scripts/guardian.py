#!/usr/bin/env python3
"""Thin shim that delegates to ``cfd_skills.cli.guardian``."""

from cfd_skills.cli.guardian import main

if __name__ == "__main__":
    raise SystemExit(main())
