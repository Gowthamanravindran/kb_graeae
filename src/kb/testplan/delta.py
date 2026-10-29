"""What changed between the base graph and the PR's graph, and which function owns each changed hunk."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from kb.graph.model import CodeGraph
from kb.ingest.checkout import Checkout
from kb.packs.model import commits_key
from kb.testplan.diff import Hunk

TS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
_DECL = {"function_declaration", "generator_function_declaration", "class_declaration", "abstract_class_declaration",
         "lexical_declaration", "variable_declaration", "interface_declaration", "type_alias_declaration",
         "enum_declaration"}


def tree_for(source: str, path: str):
    from kb.linkers.ts import _parser

    return _parser(not path.endswith(".ts")).parse(source.encode("utf-8"))


def symbol_spans(source: str, path: str) -> list[tuple[str, int, int]]:
    """Top-level declarations as (name, first line, last line), 1-based — the extent Graphify's nodes lack."""
    if not path.endswith(TS_EXTS):
        return []
    out = []
    for node in tree_for(source, path).root_node.children:
        decl = node
        if node.type == "export_statement":
            decl = node.child_by_field_name("declaration") or next(
                (c for c in node.children if c.type in _DECL), None)
            if decl is None:
                continue
        if decl.type not in _DECL:
            continue
        names = []
        if decl.type in ("lexical_declaration", "variable_declaration"):
            for c in decl.children:
                if c.type == "variable_declarator" and (n := c.child_by_field_name("name")) is not None:
                    names.append(n.text.decode())
        elif (n := decl.child_by_field_name("name")) is not None:
            names.append(n.text.decode())
        for name in names:
            out.append((name, node.start_point[0] + 1, node.end_point[0] + 1))
    return out


@dataclass
class GraphDelta:
    nodes_added: list[str] = field(default_factory=list)
    nodes_removed: list[str] = field(default_factory=list)
    nodes_changed: list[str] = field(default_factory=list)
    edges_added: list[tuple[str, str, str]] = field(default_factory=list)
    edges_removed: list[tuple[str, str, str]] = field(default_factory=list)
    hunk_owner: dict[int, str] = field(default_factory=dict)
    spans: dict[str, tuple[str, int, int]] = field(default_factory=dict)  # node id -> (side, start, end)


def head_graph(cfg, checkouts: dict[str, Checkout], work: Path) -> CodeGraph:
    """The PR's code graph (Graphify + linkers, no LLM), cached by the head commits."""
    from kb.analyze import build_graph

    key = commits_key({k: v.commit for k, v in checkouts.items()})
    cache = Path(work) / "head_graphs" / f"{key}.json"
    if cache.exists():
        return CodeGraph.from_json(json.loads(cache.read_text()))
    g = build_graph(cfg, checkouts, Path(work) / "head_build" / key).merged
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(g.to_json()))
    return g


def _read(checkouts: dict[str, Checkout], repo: str, file: str) -> str | None:
    co = checkouts.get(repo)
    if co is None:
        return None
    p = Path(co.root) / file
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _span_ids(g: CodeGraph, repo: str, file: str, source: str | None) -> list[tuple[str, int, int]]:
    """Graph node ids in `file` with their extents (only top-level symbols the graph knows)."""
    if source is None:
        return []
    known = {n.id for n in g.nodes(repo=repo) if n.file == file}
    out = []
    for name, a, b in symbol_spans(source, file):
        nid = f"{repo}:{file}#{name}"
        if nid not in known:
            nid = next((k for k in known if k.startswith(f"{nid}@") and int(k.rsplit("@", 1)[1]) == a), nid)
        if nid in known:
            out.append((nid, a, b))
    return out


def _overlaps(a: int, b: int, start: int, length: int) -> bool:
    if length == 0:  # pure insertion/deletion point: between lines start and start+1
        return a <= start < b or a <= start + 1 <= b
    return not (start + length - 1 < a or start > b)


def renamer(renames: dict[tuple[str, str], str] | None):
    """Map a base node id to its id after the PR's file moves (repo:old#X -> repo:new#X)."""
    renames = renames or {}

    def tr(i: str) -> str:
        if ":" not in i:
            return i
        repo, rest = i.split(":", 1)
        file, sep, sym = rest.partition("#")
        new = renames.get((repo, file))
        return f"{repo}:{new}{sep}{sym}" if new else i
    return tr


def graph_delta(base: CodeGraph, head: CodeGraph, hunks: list[Hunk],
                base_checkouts: dict[str, Checkout], head_checkouts: dict[str, Checkout],
                renames: dict[tuple[str, str], str] | None = None) -> GraphDelta:
    d = GraphDelta()
    tr = renamer(renames)
    base_ids = {n.id for n in base.nodes()}
    base_tr = {tr(i) for i in base_ids}
    head_ids = {n.id for n in head.nodes()}
    def symbol(g: CodeGraph, i: str) -> bool:
        return g.node(i).kind != "file" and not i.startswith("unresolved:")
    d.nodes_added = sorted(i for i in head_ids - base_tr if symbol(head, i))
    d.nodes_removed = sorted(i for i in base_ids if tr(i) not in head_ids and symbol(base, i))
    be = {(tr(e.src), tr(e.dst), e.kind) for e in base.edges()}
    he = {(e.src, e.dst, e.kind) for e in head.edges()}
    d.edges_added, d.edges_removed = sorted(he - be), sorted(be - he)

    changed: set[str] = set()
    span_cache: dict[tuple[str, str, str], list[tuple[str, int, int]]] = {}

    def spans(side: str, repo: str, file: str):
        k = (side, repo, file)
        if k not in span_cache:
            g, cos = (head, head_checkouts) if side == "head" else (base, base_checkouts)
            span_cache[k] = _span_ids(g, repo, file, _read(cos, repo, file))
            for nid, a, b in span_cache[k]:
                d.spans.setdefault(nid, (side, a, b))
        return span_cache[k]

    for i, h in enumerate(hunks):
        owners = []
        if h.new_len or h.added or not h.removed:
            owners += [(nid, a, b) for nid, a, b in spans("head", h.repo, h.file) if _overlaps(a, b, h.new_start, h.new_len)]
        if h.removed:
            owners += [(nid, a, b) for nid, a, b in spans("base", h.repo, h.old_file) if _overlaps(a, b, h.old_start, h.old_len)]
        for nid, _, _ in owners:
            changed.add(tr(nid))
        if owners:
            d.hunk_owner[i] = tr(min(owners, key=lambda o: o[2] - o[1])[0])
        else:
            d.hunk_owner[i] = f"{h.repo}:{h.file}"
    d.nodes_changed = sorted(n for n in changed if n in head_ids and n in base_tr)
    return d
