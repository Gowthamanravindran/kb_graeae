"""Linker plugins add edges that Graphify cannot see (e.g. frontend -> backend)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from kb.config import LinkerConfig
from kb.errors import ConfigError
from kb.graph.model import CodeEdge, CodeGraph
from kb.ingest.checkout import Checkout


@dataclass
class LinkContext:
    graph: CodeGraph
    checkouts: dict[str, Checkout]
    config: LinkerConfig
    ignore: tuple[str, ...] = ()


class Linker(Protocol):
    type: str

    def run(self, ctx: LinkContext) -> list[CodeEdge]: ...


_REGISTRY: dict[str, type] = {}


def register_linker(cls: type) -> type:
    _REGISTRY[cls.type] = cls
    return cls


def get_linker(type_: str) -> Linker:
    import kb.linkers.brokers  # noqa: F401  (register built-ins)
    import kb.linkers.bull_queue  # noqa: F401
    import kb.linkers.graphql  # noqa: F401
    import kb.linkers.react_router  # noqa: F401
    import kb.linkers.rest  # noqa: F401
    import kb.linkers.sidekiq  # noqa: F401

    if type_ not in _REGISTRY:
        raise ConfigError(f"unknown linker type '{type_}'. Known: {', '.join(sorted(_REGISTRY))}")
    return _REGISTRY[type_]()


def ensure_node(graph: CodeGraph, node) -> str:
    """Add a node if absent; returns its id."""
    if not graph.has_node(node.id):
        graph.add_node(node)
    return node.id


def ensure_file_node(graph: CodeGraph, repo: str, rel: str) -> str:
    from kb.graph.model import CodeNode

    return ensure_node(graph, CodeNode(id=f"{repo}:{rel}", repo=repo, kind="file", name=rel, file=rel, line=1))


Defs = dict[str, list[tuple[tuple[str, str], int]]]  # top-level name -> [((repo, file), line)]


def handler_node(graph: CodeGraph, defs: Defs, f: tuple[str, str], handler: str) -> str:
    """The function named `handler` declared nearest to file `f` (same repo, then most shared folders, then the
    shortest path); `f` itself when there is no name or no other declaration. Code often registers every handler
    in one file; pointing at that file would make it look like one place does all the work."""
    found = [d for d in defs.get(handler, ()) if d[0] != f] if handler else []
    if not found:
        return ensure_file_node(graph, f[0], f[1])

    def closeness(d):
        (drepo, drel), _ = d
        a, b = drel.split("/")[:-1], f[1].split("/")[:-1]
        shared = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
        return (drepo != f[0], -shared, len(drel), drel)
    (hrepo, hrel), line = min(found, key=closeness)
    ensure_file_node(graph, hrepo, hrel)
    from kb.graph.model import CodeNode

    return ensure_node(graph, CodeNode(id=f"{hrepo}:{hrel}#{handler}", repo=hrepo, kind="function", name=handler,
                                       file=hrel, line=line))


class SymbolIndex:
    """Graphify's own nodes for classes and methods, by file, so a linker attaches its edge to the node that impact
    analysis climbs from (Graphify names a method `<file>#.show`, with `@<line>` when a name repeats)."""

    def __init__(self, graph: CodeGraph):
        self.by_file: dict[tuple[str, str], dict[str, list[tuple[int, str]]]] = {}
        for n in graph.nodes():
            if n.kind in ("function", "variable", "class", "method"):
                name = n.name.split("@")[0].removesuffix("()")
                self.by_file.setdefault((n.repo, n.file), {}).setdefault(name, []).append((n.line or 0, n.id))
        self.methods: dict[str, set[str]] = {}
        for e in graph.edges():
            if e.kind == "method":
                self.methods.setdefault(e.src, set()).add(e.dst)

    def method(self, f: tuple[str, str], cls: str, method: str) -> str | None:
        names = self.by_file.get(f, {})
        found = names.get(f".{method}", []) + names.get(method, [])
        if not found:
            return None
        if len(found) > 1 and cls:   # the class's own method, by Graphify's class -> method edges
            short = cls.rsplit("::", 1)[-1].rsplit(".", 1)[-1]
            for nm, ids in names.items():
                if nm == cls or nm.endswith(f"::{short}") or nm == short:
                    for _, cid in ids:
                        mine = [i for _, i in found if i in self.methods.get(cid, ())]
                        if mine:
                            return mine[0]
        return min(found)[1]


def method_node(graph: CodeGraph, f: tuple[str, str], cls: str, method: str, line: int,
                index: SymbolIndex | None = None) -> str:
    """The node for `cls#method` in file `f`: Graphify's when it has one, else a new function node."""
    from kb.graph.model import CodeNode

    if index is not None and (found := index.method(f, cls, method)) is not None:
        return found
    ensure_file_node(graph, f[0], f[1])
    return ensure_node(graph, CodeNode(id=f"{f[0]}:{f[1]}#{cls}.{method}", repo=f[0], kind="function",
                                       name=f"{cls}.{method}", file=f[1], line=line))
