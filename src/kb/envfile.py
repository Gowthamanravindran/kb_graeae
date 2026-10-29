"""Settings file `$KB_HOME/kb.env` (default ~/.kb/kb.env): KEY=VALUE lines, e.g. Langfuse keys.

Lets the desktop app (opened from Finder, which passes no shell exports) and the CLI share one
place for settings. Variables already in the environment always win.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_applied: set[str] = set()  # keys whose os.environ value came from kb.env (load) or the Settings screen

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
            _applied.add(key)
    return applied


def load_kb_env(kb_home: Path | None = None) -> list[str]:
    from kb.packs.store import default_kb_home

    return load_env_file(Path(kb_home or default_kb_home()) / "kb.env")


def read_env_file(path: Path) -> dict[str, str]:
    """KEY -> value of the file as written (quotes removed); later lines win."""
    out: dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return out
    for raw in text.splitlines():
        m = _LINE.match(raw)
        if m and not raw.lstrip().startswith("#"):
            v = m.group(2)
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            out[m.group(1)] = v
    return out


def write_env_file(path: Path, updates: dict[str, str | None]) -> None:
    """Set (or, with None, remove) keys; comments, other keys and order are kept; new keys go at the end.
    The file is written atomically with mode 0600 because it holds secrets."""
    path = Path(path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    done: set[str] = set()
    out: list[str] = []
    for raw in lines:
        m = _LINE.match(raw)
        key = m.group(1) if m and not raw.lstrip().startswith("#") else None
        if key in updates:
            if updates[key] is not None and key not in done:
                out.append(f"{key}={updates[key]}")
            done.add(key)
            continue
        out.append(raw)
    out += [f"{k}={v}" for k, v in updates.items() if v is not None and k not in done]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("".join(line + "\n" for line in out))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def env_source(path: Path, key: str) -> str | None:
    """'environment' when the process environment set it (that wins), 'file' when kb.env has it, else None."""
    if key in os.environ and key not in _applied:
        return "environment"
    return "file" if key in read_env_file(path) else None


def apply_setting(key: str, value: str | None) -> None:
    """Make a saved value live now, unless the process environment owns the key."""
    if key in os.environ and key not in _applied:
        return
    if value is None:
        os.environ.pop(key, None)
        _applied.discard(key)
    else:
        os.environ[key] = value
        _applied.add(key)
