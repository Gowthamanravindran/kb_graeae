"""`git diff base...head` for one repo, as hunks with their added and removed lines."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from kb.ingest.checkout import _git

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class Hunk:
    repo: str
    file: str
    old_file: str
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


def _strip(path: str) -> str:
    return path[2:] if path[:2] in ("a/", "b/") else path


def parse_diff(text: str, repo: str) -> list[Hunk]:
    hunks: list[Hunk] = []
    old = new = None
    cur: Hunk | None = None
    for line in text.splitlines():
        if line.startswith("diff --git "):
            old = new = None
            cur = None
        elif line.startswith("rename from "):
            old = line[len("rename from "):]
        elif line.startswith("rename to "):
            new = line[len("rename to "):]
        elif line.startswith("--- "):
            p = line[4:]
            old = None if p == "/dev/null" else _strip(p)
        elif line.startswith("+++ "):
            p = line[4:]
            new = None if p == "/dev/null" else _strip(p)
        elif (m := _HUNK.match(line)):
            a, b, c, d = m.groups()
            cur = Hunk(repo=repo, file=new or old or "", old_file=old or new or "",
                       old_start=int(a), old_len=int(b) if b is not None else 1,
                       new_start=int(c), new_len=int(d) if d is not None else 1)
            hunks.append(cur)
        elif cur is not None and line.startswith("+"):
            cur.added.append(line[1:])
        elif cur is not None and line.startswith("-"):
            cur.removed.append(line[1:])
    return hunks


def repo_diff(source: Path, repo: str, base: str, head: str, ignore: list[str], subpath: str | None = None) -> list[Hunk]:
    """Hunks between two refs (renames followed). `subpath` (the repo's `path:`) limits and re-roots file paths."""
    from kb.globs import matches_any

    text = _git(Path(source), "diff", "-M", "-U0", "--no-color", f"{base}...{head}")
    out = []
    prefix = (subpath.strip("/") + "/") if subpath else ""
    for h in parse_diff(text, repo):
        if prefix:
            if not (h.file.startswith(prefix) or h.old_file.startswith(prefix)):
                continue
            h.file = h.file[len(prefix):] if h.file.startswith(prefix) else h.file
            h.old_file = h.old_file[len(prefix):] if h.old_file.startswith(prefix) else h.old_file
        if matches_any(h.file, list(ignore)):
            continue
        out.append(h)
    return out
