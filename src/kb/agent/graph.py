"""understand -> collect -> trace -> verify -> render -> learn, with shortcuts:
a fresh saved flow (reuse) or no match (not found) goes straight to render and skips learn."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph

from kb.agent import nodes
from kb.agent.state import AgentState

ORDER = ["understand", "collect", "trace", "verify", "render", "learn"]


@dataclass
class AgentDeps:
    pack: Any
    pack_dir: Path | None
    checkouts: dict
    provider: Any
    tracer: Any
    store: Any
    author: str
    context: str = ""
    extra: dict = field(default_factory=dict)


def _bind(name: str, deps: AgentDeps):
    fn = getattr(nodes, name)  # looked up at build time so tests can replace nodes

    def run(state: AgentState) -> dict:
        return fn(state, deps) or {}

    run.__name__ = name
    return run


def _after_understand(state: AgentState) -> str:
    return "render" if state.get("reused_flow_id") or state.get("not_found") else "collect"


def _after_render(state: AgentState) -> str:
    return END if state.get("reused_flow_id") or state.get("not_found") else "learn"


def build_agent(deps: AgentDeps):
    g = StateGraph(AgentState)
    for n in ORDER:
        g.add_node(n, _bind(n, deps))
    g.add_edge(START, "understand")
    g.add_conditional_edges("understand", _after_understand, {"render": "render", "collect": "collect"})
    g.add_edge("collect", "trace")
    g.add_edge("trace", "verify")
    g.add_edge("verify", "render")
    g.add_conditional_edges("render", _after_render, {"learn": "learn", END: END})
    g.add_edge("learn", END)
    return g.compile()
