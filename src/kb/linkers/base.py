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


def method_node(graph: CodeGraph, f: tuple[str, str], cls: str, method: str, line: int) -> str:
    from kb.graph.model import CodeNode

    ensure_file_node(graph, f[0], f[1])
    return ensure_node(graph, CodeNode(id=f"{f[0]}:{f[1]}#{cls}.{method}", repo=f[0], kind="function",
                                       name=f"{cls}.{method}", file=f[1], line=line))
