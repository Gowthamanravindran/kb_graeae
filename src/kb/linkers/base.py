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
    import kb.linkers.bull_queue  # noqa: F401  (register built-ins)
    import kb.linkers.graphql  # noqa: F401
    import kb.linkers.react_router  # noqa: F401

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
