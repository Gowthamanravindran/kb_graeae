"""What changed between the base graph and the PR's graph, and which function owns each changed hunk."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
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


RB_EXTS = (".rb", ".rake")
CODE_EXTS = TS_EXTS + RB_EXTS   # languages whose decision points the inventory reads


def _ruby_spans(source: str) -> list[tuple[str, int, int]]:
    """Ruby classes and modules by their full name (`Api::V1::UsersController`) and methods as `.name`, matching
    Graphify's node names."""
    from kb.linkers import rb

    out = []

    def visit(node, prefix: str):
        for c in node.named_children:
            if c.type in ("class", "module"):
                nm = rb.const_path(c.child_by_field_name("name"))
                full = f"{prefix}::{nm}" if prefix and nm else nm or prefix
                out.append((full, c.start_point[0] + 1, c.end_point[0] + 1))
                visit(c, full)
            elif c.type in ("method", "singleton_method"):
                if (mn := c.child_by_field_name("name")) is not None:
                    out.append((f".{mn.text.decode()}", c.start_point[0] + 1, c.end_point[0] + 1))
            elif c.named_children:
                visit(c, prefix)

    visit(rb.parse_text(source).root_node, "")
    return out


def symbol_spans(source: str, path: str) -> list[tuple[str, int, int]]:
    """Top-level declarations as (name, first line, last line), 1-based — the extent Graphify's nodes lack."""
    if path.endswith(RB_EXTS):
        return _ruby_spans(source)
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
        if decl.type in ("class_declaration", "abstract_class_declaration"):
            out += _method_spans(decl)
    return out


_FN_VALUES = {"arrow_function", "function_expression", "function"}


def _method_spans(cls) -> list[tuple[str, int, int]]:
    """A class's methods as `.name` — the id Graphify gives them — so a change inside one method is owned
    by that method, not by the whole class."""
    body = cls.child_by_field_name("body")
    out = []
    for m in (body.named_children if body is not None else []):
        name = m.child_by_field_name("name") or m.child_by_field_name("property")
        if name is None:
            continue
        if m.type == "method_definition" or (
                m.type in ("public_field_definition", "field_definition")
                and (v := m.child_by_field_name("value")) is not None and v.type in _FN_VALUES):
            out.append((f".{name.text.decode()}", m.start_point[0] + 1, m.end_point[0] + 1))
    return out


def find_span(source: str | None, path: str, name: str, near: int | None = None) -> tuple[int, int] | None:
    """The span of symbol `name` (`f`, `.method`, or `.method@<line>` when a file has two of the same name).
    `near` is a line known to be inside it (a hunk's start on this side), which beats `@line`: the line in an
    id comes from the head graph and is off on the base side once lines shift."""
    if source is None:
        return None
    bare, _, at = name.partition("@")
    hits = [(a, b) for n, a, b in symbol_spans(source, path) if n == bare]
    if not hits:
        return None
    if near is not None:
        inside = [h for h in hits if h[0] <= near <= h[1]]
        if inside:
            return min(inside, key=lambda h: h[1] - h[0])
    if at.isdigit():
        return min(hits, key=lambda h: abs(h[0] - int(at)))
    return hits[0]


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

    key = f"{commits_key({k: v.commit for k, v in checkouts.items()})}-{config_key(cfg)}"
    cache = Path(work) / "head_graphs" / f"{key}.json"
    if cache.exists():
        os.utime(cache)  # recently used: kept by prune_work
        return CodeGraph.from_json(json.loads(cache.read_text()))
    build = Path(work) / "head_build" / key
    try:
        g = build_graph(cfg, checkouts, build).merged
    finally:
        shutil.rmtree(build, ignore_errors=True)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(g.to_json()))
    return g


def config_key(cfg) -> str:
    """What in kb.yaml changes the graph: ignore patterns, linkers, and each repo's sub-path."""
    data = {"ignore": list(cfg.ignore), "linkers": [lk.model_dump(mode="json") for lk in cfg.linkers],
            "repos": sorted((r.name, r.path or "") for r in cfg.repos)}
    return hashlib.sha1(json.dumps(data, sort_keys=True).encode()).hexdigest()[:8]


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
        # only the innermost code a change sits in: a method, not also the class around it (whose other
        # methods' callers would otherwise all count as affected); hunks spanning two functions keep both
        inner = [o for o in owners if not any(p is not o and o[1] <= p[1] and p[2] <= o[2] and (p[1], p[2]) != (o[1], o[2])
                                               for p in owners)]
        for nid, _, _ in inner:
            changed.add(tr(nid))
        if owners:
            d.hunk_owner[i] = tr(min(owners, key=lambda o: o[2] - o[1])[0])
        else:
            d.hunk_owner[i] = f"{h.repo}:{h.file}"
    d.nodes_changed = sorted(n for n in changed if n in head_ids and n in base_tr)
    return d
