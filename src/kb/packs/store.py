"""Local pack storage. Layout follows spec 3.2:
<kb_home>/packs/<project>@<branch with / -> __>/
  manifest.json, base/{graph/<repo>.json, links.json, modules.json, system.md},
  overlay/{qa/, flows/, module_notes/, suggestions.json}, index.sqlite
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path

from kb.errors import KbError
from kb.graph.model import CodeEdge, CodeGraph, CodeNode
from kb.packs.filter import sanitize_graph
from kb.packs.model import Flow, Manifest, Module, QAItem, Suggestion


@dataclass
class Pack:
    dir: Path
    manifest: Manifest
    graph: CodeGraph
    modules: list[Module]
    system_md: str
    flows: list[Flow] = field(default_factory=list)
    qa: list[QAItem] = field(default_factory=list)
    suggestions: list[Suggestion] = field(default_factory=list)
    module_notes: dict[str, str] = field(default_factory=dict)

    def module(self, module_id: str) -> Module:
        return next(m for m in self.modules if m.id == module_id)


def default_kb_home() -> Path:
    return Path(os.environ.get("KB_HOME", Path.home() / ".kb"))


_overlay_locks: dict[Path, threading.Lock] = {}
_locks_guard = threading.Lock()


def _overlay_lock(path: Path) -> threading.Lock:
    """One in-process lock per overlay file, for read-modify-write (kb serve runs jobs in threads)."""
    key = Path(path).resolve()
    with _locks_guard:
        return _overlay_locks.setdefault(key, threading.Lock())


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, default=str))
    tmp.replace(path)


def check_unsafe_paths(pack_dir: Path, paths) -> None:
    """Raise KbError unless every path is an allowed pack base path that stays inside pack_dir/base."""
    from kb_registry.paths import is_allowed_path

    base = (Path(pack_dir) / "base").resolve()
    for rel in paths:
        target = (Path(pack_dir) / rel).resolve()
        if rel == "manifest.json" or not is_allowed_path(rel) or not target.is_relative_to(base):
            raise KbError(f"unsafe path from registry: {rel!r}")


def _overlay_file(pack_dir: Path, sub: str, item_id: str) -> Path:
    from kb_registry.paths import is_safe_id

    if not is_safe_id(item_id):
        raise KbError(f"unsafe overlay id: {item_id!r}")
    return Path(pack_dir) / "overlay" / sub / f"{item_id}.json"


class PackStore:
    def __init__(self, kb_home: Path | None = None) -> None:
        self.kb_home = Path(kb_home) if kb_home else default_kb_home()

    def pack_dir(self, project: str, branch: str) -> Path:
        return self.kb_home / "packs" / f"{project}@{branch.replace('/', '__')}"

    def exists(self, project: str, branch: str) -> bool:
        return (self.pack_dir(project, branch) / "manifest.json").exists()

    def write_base(self, project: str, branch: str, manifest: Manifest, graphs: dict[str, CodeGraph],
                   links: list[CodeEdge], modules: list[Module], system_md: str,
                   link_nodes: list[CodeNode] = ()) -> Path:
        d = self.pack_dir(project, branch)
        base = d / "base"
        if base.exists():
            shutil.rmtree(base)
        for repo, g in graphs.items():
            _write_json(base / "graph" / f"{repo}.json", sanitize_graph(g))
        _write_json(base / "links.json", {
            "nodes": [n.model_dump() for n in link_nodes],
            "edges": [e.model_dump() for e in links],
        })
        _write_json(base / "modules.json", [m.model_dump() for m in modules])
        (base / "system.md").write_text(system_md)
        for sub in ("qa", "flows", "module_notes"):
            (d / "overlay" / sub).mkdir(parents=True, exist_ok=True)
        _write_json(d / "manifest.json", manifest.model_dump(mode="json"))
        return d

    def write_pulled_base(self, project: str, branch: str, manifest: Manifest, files: dict[str, bytes]) -> Path:
        """Replace base/ with files from the registry; overlay/ is kept. Every path is checked first."""
        from kb_registry.paths import is_allowed_path

        d = self.pack_dir(project, branch)
        check_unsafe_paths(d, files)
        tmp, old = d / "base.tmp", d / "base.old"
        for x in (tmp, old):
            if x.exists():
                shutil.rmtree(x)
        for rel, data in files.items():
            target = tmp / rel.removeprefix("base/")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        if (d / "base").exists():
            (d / "base").rename(old)
        tmp.rename(d / "base")
        if old.exists():
            shutil.rmtree(old)
        for sub in ("qa", "flows", "module_notes"):
            (d / "overlay" / sub).mkdir(parents=True, exist_ok=True)
        _write_json(d / "manifest.json", manifest.model_dump(mode="json"))
        return d

    def write_modules(self, project: str, branch: str, modules: list[Module], system_md: str) -> None:
        base = self.pack_dir(project, branch) / "base"
        _write_json(base / "modules.json", [m.model_dump() for m in modules])
        (base / "system.md").write_text(system_md)

    def update_manifest(self, project: str, branch: str, **changes) -> Manifest:
        p = self.pack_dir(project, branch) / "manifest.json"
        m = Manifest.model_validate({**json.loads(p.read_text()), **changes})
        _write_json(p, m.model_dump(mode="json"))
        return m

    def load(self, project: str, branch: str) -> Pack:
        d = self.pack_dir(project, branch)
        if not (d / "manifest.json").exists():
            raise KbError(f"No analysed pack for {project}@{branch}. Run kb analyze first.")
        manifest = Manifest.model_validate_json((d / "manifest.json").read_text())
        g = CodeGraph()
        for f in sorted((d / "base" / "graph").glob("*.json")):
            g.merge(CodeGraph.from_json(json.loads(f.read_text())))
        links = json.loads((d / "base" / "links.json").read_text())
        for n in links.get("nodes", []):
            node = CodeNode.model_validate(n)
            if not g.has_node(node.id):
                g.add_node(node)
        for e in links.get("edges", []):
            edge = CodeEdge.model_validate(e)
            if g.has_node(edge.src) and (not edge.resolved or g.has_node(edge.dst)):
                g.add_edge(edge)
        modules = [Module.model_validate(m) for m in json.loads((d / "base" / "modules.json").read_text())]
        ov = d / "overlay"
        sug = ov / "suggestions.json"
        return Pack(
            dir=d, manifest=manifest, graph=g, modules=modules, system_md=(d / "base" / "system.md").read_text(),
            flows=[Flow.model_validate_json(f.read_text()) for f in sorted((ov / "flows").glob("*.json"))],
            qa=[QAItem.model_validate_json(f.read_text()) for f in sorted((ov / "qa").glob("*.json"))],
            suggestions=[Suggestion.model_validate(s) for s in json.loads(sug.read_text())] if sug.exists() else [],
            module_notes={f.stem: json.loads(f.read_text())["text"] for f in sorted((ov / "module_notes").glob("*.json"))},
        )

    def save_flow(self, project: str, branch: str, flow: Flow) -> None:
        _write_json(_overlay_file(self.pack_dir(project, branch), "flows", flow.id), flow.model_dump(mode="json"))

    def save_qa(self, project: str, branch: str, qa: QAItem) -> None:
        _write_json(_overlay_file(self.pack_dir(project, branch), "qa", qa.id), qa.model_dump(mode="json"))

    def add_suggestions(self, project: str, branch: str, items: list[Suggestion]) -> None:
        p = self.pack_dir(project, branch) / "overlay" / "suggestions.json"
        with _overlay_lock(p):
            existing = json.loads(p.read_text()) if p.exists() else []
            seen = {s["question"].lower() for s in existing}
            existing += [s.model_dump() for s in items if s.question.lower() not in seen]
            _write_json(p, existing)

    def save_module_note(self, project: str, branch: str, module_id: str, text: str) -> None:
        _write_json(_overlay_file(self.pack_dir(project, branch), "module_notes", module_id), {"text": text})
