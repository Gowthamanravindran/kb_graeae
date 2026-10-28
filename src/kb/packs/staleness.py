from __future__ import annotations

import hashlib
from pathlib import Path

from kb.packs.model import Citation


def hash_span(root: Path, file: str, start: int, end: int) -> str:
    lines = (Path(root) / file).read_text(errors="replace").splitlines()
    chunk = "\n".join(lines[start - 1:end])
    return hashlib.sha256(chunk.encode()).hexdigest()[:16]


def is_stale(c: Citation, checkouts: dict) -> bool:
    """True if the cited lines changed or the file is gone. Unknown (no checkout) is not stale."""
    co = checkouts.get(c.repo)
    if co is None:
        return False
    path = Path(co.root) / c.file
    if not path.is_file():
        return True
    return hash_span(co.root, c.file, c.line_start, c.line_end) != c.content_hash
