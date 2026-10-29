"""Publishes waiting for the registry: $KB_HOME/outbox/<project>@<branch>.json."""

from __future__ import annotations

import json
from pathlib import Path


class Outbox:
    def __init__(self, kb_home: Path) -> None:
        self.dir = Path(kb_home) / "outbox"

    def _path(self, project: str, branch: str) -> Path:
        return self.dir / f"{project}@{branch.replace('/', '__')}.json"

    def pending(self, project: str, branch: str) -> list[dict]:
        p = self._path(project, branch)
        return json.loads(p.read_text()) if p.exists() else []

    def add(self, project: str, branch: str, items: list[dict]) -> None:
        existing = {i["id"]: i for i in self.pending(project, branch)}
        existing.update({i["id"]: i for i in items})
        self._write(project, branch, list(existing.values()))

    def clear(self, project: str, branch: str, ids) -> None:
        drop = set(ids)
        self._write(project, branch, [i for i in self.pending(project, branch) if i["id"] not in drop])

    def _rejected_path(self, project: str, branch: str) -> Path:
        return self.dir / f"{project}@{branch.replace('/', '__')}.rejected.json"

    def rejected(self, project: str, branch: str) -> list[dict]:
        p = self._rejected_path(project, branch)
        return json.loads(p.read_text()) if p.exists() else []

    def reject(self, project: str, branch: str, items: list[dict], reason: str) -> None:
        """Move items the registry refused out of the queue, so they stop blocking every sync."""
        p = self._rejected_path(project, branch)
        kept = self.rejected(project, branch) + [{**i, "reason": reason} for i in items]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(kept, indent=1))
        self.clear(project, branch, [i["id"] for i in items])

    def _write(self, project: str, branch: str, items: list[dict]) -> None:
        p = self._path(project, branch)
        if not items:
            p.unlink(missing_ok=True)
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, indent=1))
        tmp.replace(p)
