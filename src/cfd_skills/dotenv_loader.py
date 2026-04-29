"""Tiny zero-dep ``.env`` loader for CLI entry points.

Loads ``KEY=value`` pairs into ``os.environ`` via ``setdefault`` so that
real shell env vars always take precedence over ``.env`` content. Missing
files are a no-op. Supports comments (``#``), blank lines, single/double
quoted values, and an optional ``export`` prefix (so users can paste bash
snippets verbatim).

Why this exists: skill 4 (``session-news-brief``) reads three news API
keys from ``os.environ``. On Windows / PowerShell the bash ``export``
syntax doesn't work, so ``.env`` is the more portable way to get keys
into the process. Committed-to-repo template lives in ``.env.example``;
``.env`` itself is gitignored.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: str | Path) -> dict[str, str]:
    """Parse a ``.env`` at ``path`` and apply to ``os.environ``.

    Real environment variables already set in the process always win
    (we use ``setdefault``). Returns the dict of keys that were *parsed*
    from the file (not necessarily applied), useful for diagnostics.
    Missing or unreadable files return an empty dict.
    """
    p = Path(path)
    if not p.is_file():
        return {}

    parsed: dict[str, str] = {}
    text = p.read_text(encoding="utf-8")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # Allow `export KEY=value` for bash-snippet compatibility.
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or any(c.isspace() for c in key):
            # Empty or whitespace in key → not a real env line, skip.
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        parsed[key] = value
        os.environ.setdefault(key, value)
    return parsed
