"""Turning a local pack into registry uploads and back."""

from __future__ import annotations

from pathlib import Path

from kb_registry.paths import is_allowed_path


def base_files(pack_dir: Path) -> dict[str, bytes]:
    """The base files a push uploads: every allowed path except manifest.json (sent as JSON)."""
    pack_dir = Path(pack_dir)
    out: dict[str, bytes] = {}
    for p in sorted((pack_dir / "base").rglob("*")):
        rel = p.relative_to(pack_dir).as_posix()
        if p.is_file() and rel != "manifest.json" and is_allowed_path(rel):
            out[rel] = p.read_bytes()
    return out
