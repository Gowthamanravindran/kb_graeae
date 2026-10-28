"""Run Graphify (code-only, no LLM) and convert its graph.json into a CodeGraph.

Captured schema (graphify 0.9.x, `extract --no-cluster`), see tests/fixtures/graphify:
  top level: {"nodes": [...], "edges": [...]}   (clustered output uses "links")
  node: id, label ("scheduleRepeat()" for callables, file basename or "dir/file.ts" for files),
        source_file (relative to the scanned dir), source_location ("L18"), _origin
        external references have no source_file and are dropped.
  edge: source, target, relation (calls|contains|imports|imports_from|indirect_call|...),
        confidence (EXTRACTED|INFERRED|AMBIGUOUS). Graphify marks the graph undirected,
        but source -> target is the direction of the relation; we keep it.
Mapping: CodeNode.id = "<repo>:<file>" for files, "<repo>:<file>#<name>" for symbols
(suffix "@<line>" if a name repeats in a file); EXTRACTED -> origin "extracted", else "inferred".
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath

from kb.errors import KbError
from kb.globs import matches_any
from kb.graph.model import CodeEdge, CodeGraph, CodeNode
from kb.ingest.checkout import Checkout

_LINE = re.compile(r"L(\d+)")


def run_graphify(checkout: Checkout, out_dir: Path, ignore: list[str]) -> Path:
    """Extract into out_dir (never into the repo). `ignore` is applied when parsing."""
    if shutil.which("graphify") is None:
        raise KbError("graphify not found: uv tool install graphifyy")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "GRAPHIFY_BACKEND": "none"}
    args = ["graphify", "extract", ".", "--code-only", "--no-cluster", "--out", str(out_dir)]
    r = subprocess.run(args, cwd=checkout.root, env=env, capture_output=True, text=True)
    if r.returncode != 0:
        raise KbError(f"graphify failed on {checkout.repo}: {(r.stderr or r.stdout).strip()[-500:]}")
    path = out_dir / "graphify-out" / "graph.json"
    if not path.exists():
        raise KbError(f"graphify produced no graph.json for {checkout.repo}")
    return path


def _kind(label: str, file: str) -> str:
    if label.endswith("()"):
        name = label[:-2]
        if file.endswith((".tsx", ".jsx")) and name[:1].isupper():
            return "component"
        return "function"
    if PurePosixPath(label).name == PurePosixPath(file).name:
        return "file"
    if file.endswith((".tsx", ".jsx")) and label[:1].isupper():
        return "component"
    return "variable"


def parse_graphify_json(path: Path, repo: str, repo_root: Path, ignore: list[str] = ()) -> CodeGraph:
    raw = json.loads(Path(path).read_text())
    g = CodeGraph()
    id_map: dict[str, str] = {}
    used: set[str] = set()
    root = Path(repo_root).resolve()

    for n in raw.get("nodes", []):
        src = n.get("source_file")
        if not src:
            continue
        p = Path(src)
        rel = p.resolve().relative_to(root).as_posix() if p.is_absolute() else PurePosixPath(src).as_posix()
        if matches_any(rel, list(ignore)):
            continue
        label = str(n.get("label") or n["id"])
        kind = _kind(label, rel)
        m = _LINE.match(str(n.get("source_location") or ""))
        line = int(m.group(1)) if m else None
        if kind == "file":
            nid, name = f"{repo}:{rel}", rel
        else:
            name = label[:-2] if label.endswith("()") else label
            nid = f"{repo}:{rel}#{name}"
            if nid in used:
                nid = f"{nid}@{line}"
        used.add(nid)
        id_map[n["id"]] = nid
        g.add_node(CodeNode(id=nid, repo=repo, kind=kind, name=name, file=rel, line=line))

    for e in raw.get("edges", raw.get("links", [])):
        s, t = id_map.get(e.get("source")), id_map.get(e.get("target"))
        if not s or not t:
            continue
        origin = "extracted" if e.get("confidence", "EXTRACTED") == "EXTRACTED" else "inferred"
        g.add_edge(CodeEdge(src=s, dst=t, kind=str(e.get("relation", "related")), origin=origin))
    return g
