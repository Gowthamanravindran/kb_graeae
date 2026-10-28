"""Settings file `$KB_HOME/kb.env` (default ~/.kb/kb.env): KEY=VALUE lines, e.g. Langfuse keys.

Lets the desktop app (opened from Finder, which passes no shell exports) and the CLI share one
place for settings. Variables already in the environment always win.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def load_env_file(path: Path) -> list[str]:
    """Apply KEY=VALUE lines from `path` that are not already set; returns the names applied."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return []
    applied = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        m = _LINE.match(raw)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value
            applied.append(key)
    return applied


def load_kb_env(kb_home: Path | None = None) -> list[str]:
    from kb.packs.store import default_kb_home

    return load_env_file(Path(kb_home or default_kb_home()) / "kb.env")
