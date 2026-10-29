"""Graph 1 — Impact (spec §3.1): resolve_change_set → (diff ∥ head_graph) → graph_delta → inventory → reach
→ name_flows → plan_slots → full_reanalysis_check → save_impact. No Claude except one naming call."""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from kb.testplan import nodes_impact as n
from kb.testplan.models import ImpactReport
from kb.testplan.nodes_impact import ChangeSetRequest, ImpactDeps  # noqa: F401  (re-exported)


class ImpactState(TypedDict, total=False):
    request: Any
    change_set: Any
    label: str
    base_cos: dict
    head_cos: dict
    work: Any
    hunks: list
    renames: dict
    head_graph: Any
    base_graph: Any
    delta: Any
    inventory: list
    flows: list
    slots: list
    needs_full: bool
    full_reason: str
    impact: Any


ORDER = [("resolve_change_set", n.resolve_change_set), ("diff", n.diff), ("head_graph", n.head_graph_node),
         ("graph_delta", n.delta), ("inventory", n.inventory_node), ("reach", n.reach_node),
         ("name_flows", n.name_flows), ("plan_slots", n.plan_slots_node),
         ("full_reanalysis_check", n.full_reanalysis_check), ("save_impact", n.save_impact)]


def build_impact_graph(deps: ImpactDeps):
    g = StateGraph(ImpactState)
    for name, fn in ORDER:
        g.add_node(name, (lambda f: (lambda s: f(s, deps) or {}))(fn))
    g.add_edge(START, "resolve_change_set")
    g.add_edge("resolve_change_set", "diff")
    g.add_edge("resolve_change_set", "head_graph")
    g.add_edge(["diff", "head_graph"], "graph_delta")  # waits for both branches
    for a, b in zip(ORDER[3:], ORDER[4:]):
        g.add_edge(a[0], b[0])
    g.add_edge("save_impact", END)
    return g.compile()


def run_impact(cfg, req: ChangeSetRequest, store, provider, tracer, progress=None, author: str = "unknown") -> ImpactReport:
    deps = ImpactDeps(cfg=cfg, store=store, provider=provider, tracer=tracer, author=author,
                      report=progress or (lambda text, frac: None))
    with tracer.trace("testplan_impact", {"project": cfg.project}) as root:
        state = build_impact_graph(deps).invoke({"request": req}, config={"callbacks": tracer.langgraph_callbacks()})
        root.end(output={"flows": len(state["impact"].flows), "inventory": len(state["impact"].inventory)})
    tracer.flush()
    return state["impact"]
