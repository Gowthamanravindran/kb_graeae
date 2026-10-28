"""Language-neutral code graph: nodes are files/symbols, edges are relations."""

from __future__ import annotations

from typing import Literal

import networkx as nx
from pydantic import BaseModel

Origin = Literal["extracted", "inferred", "linker"]
Direction = Literal["out", "in", "both"]


class CodeNode(BaseModel):
    id: str
    repo: str
    kind: str
    name: str
    file: str
    line: int | None = None
    end_line: int | None = None


class CodeEdge(BaseModel):
    src: str
    dst: str
    kind: str
    origin: Origin
    resolved: bool = True
    label: str | None = None


class CodeGraph:
    def __init__(self) -> None:
        self._g = nx.MultiDiGraph()
        self._unresolved: list[CodeEdge] = []

    def add_node(self, node: CodeNode) -> None:
        self._g.add_node(node.id, data=node)

    def has_node(self, node_id: str) -> bool:
        return node_id in self._g

    def add_edge(self, edge: CodeEdge) -> None:
        if edge.src not in self._g:
            raise KeyError(edge.src)
        if not edge.resolved:
            self._unresolved.append(edge)
            return
        if edge.dst not in self._g:
            raise KeyError(edge.dst)
        self._g.add_edge(edge.src, edge.dst, data=edge)

    def node(self, node_id: str) -> CodeNode:
        return self._g.nodes[node_id]["data"]

    def nodes(self, repo: str | None = None, kind: str | None = None) -> list[CodeNode]:
        out = [d["data"] for _, d in self._g.nodes(data=True)]
        return [x for x in out if (repo is None or x.repo == repo) and (kind is None or x.kind == kind)]

    def edges(self, kind: str | None = None) -> list[CodeEdge]:
        out = [d["data"] for _, _, d in self._g.edges(data=True)] + self._unresolved
        return [e for e in out if kind is None or e.kind == kind]

    def neighbors(self, node_id: str, kinds: set[str] | None = None, direction: Direction = "out") -> list[CodeNode]:
        seen: dict[str, CodeNode] = {}
        if direction in ("out", "both"):
            for _, dst, d in self._g.out_edges(node_id, data=True):
                if kinds is None or d["data"].kind in kinds:
                    seen.setdefault(dst, self.node(dst))
        if direction in ("in", "both"):
            for src, _, d in self._g.in_edges(node_id, data=True):
                if kinds is None or d["data"].kind in kinds:
                    seen.setdefault(src, self.node(src))
        return list(seen.values())

    def merge(self, other: "CodeGraph") -> None:
        for x in other.nodes():
            self.add_node(x)
        for e in other.edges():
            self.add_edge(e)

    @property
    def nx(self) -> nx.MultiDiGraph:
        return self._g

    def to_json(self) -> dict:
        return {
            "nodes": [x.model_dump() for x in sorted(self.nodes(), key=lambda x: x.id)],
            "edges": [e.model_dump() for e in sorted(self.edges(), key=lambda e: (e.src, e.dst, e.kind))],
        }

    @classmethod
    def from_json(cls, d: dict) -> "CodeGraph":
        g = cls()
        for x in d.get("nodes", []):
            g.add_node(CodeNode.model_validate(x))
        for e in d.get("edges", []):
            g.add_edge(CodeEdge.model_validate(e))
        return g
