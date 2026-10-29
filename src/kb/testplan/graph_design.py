"""Graph 2 — Test design (spec §3.2): plan_work → Send(flow) per flow → merge → coverage → save_plan.
Each flow: (baseline ∥ head trace) → compare → scenarios → cases → check_cases, saved as soon as it finishes;
one flow failing marks only its slots failed."""

from __future__ import annotations

import json
import operator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import BaseModel, Field

from kb.errors import KbError
from kb.packs.model import Flow, commits_key
from kb.testplan.models import PRIORITIES, CaseSlot
from kb.testplan.nodes_design import DesignWork, baseline_flow, check_cases, compare, head_flow, write_cases, write_scenarios
from kb.testplan.slots import select
from kb.testplan.store import PlanStore

Mode = Literal["pr", "more", "scenario"]


class Coverage(BaseModel):
    items: dict[str, str] = Field(default_factory=dict)
    by_priority: dict[str, int] = Field(default_factory=dict)
    by_status: dict[str, int] = Field(default_factory=dict)


class DesignResult(BaseModel):
    written: int = 0
    failed: int = 0
    extras: int = 0
    coverage: Coverage = Field(default_factory=Coverage)


class DesignState(TypedDict, total=False):
    work_ids: list[str]
    flow_id: str
    results: Annotated[list[dict], operator.add]
    result: Any


@dataclass
class _Ctx:
    cfg: Any
    store: Any
    ps: PlanStore
    pid: str
    label: str
    mode: str
    provider: Any
    tracer: Any
    author: str
    report: Any
    works: dict[str, DesignWork] = field(default_factory=dict)
    reserved: set = field(default_factory=set)
    chosen: dict[str, list[CaseSlot]] = field(default_factory=dict)
    commits: str = ""
    total: int = 0
    done: list[str] = field(default_factory=list)


def _checkouts(ctx: _Ctx, cs) -> tuple[dict, dict]:
    if cs.kind == "scenario":
        from kb.ask import available_checkouts

        pack = ctx.store.load(ctx.cfg.project, ctx.label)
        cos, _ = available_checkouts(ctx.cfg, ctx.label, ctx.store, pack.manifest.commits, pack.manifest.branches)
        return cos, cos
    from kb.testplan.worktrees import worktree_at

    work = ctx.store.kb_home / "work" / ctx.cfg.project
    base = {r: worktree_at(ctx.cfg.repo(r), ref.commit, work) for r, ref in cs.base.items()}
    head = {r: (worktree_at(ctx.cfg.repo(r), cs.head[r].commit, work) if cs.head[r].commit != ref.commit else base[r])
            for r, ref in cs.base.items()}
    return base, head


def _changed_code(ctx: _Ctx, cs, flow) -> str:
    if cs.kind == "scenario":
        return ""
    from kb.testplan.diff import repo_diff
    from kb.testplan.worktrees import git_source

    files = {(c.split(":", 1)[0], c.split(":", 1)[1].split("#", 1)[0]) for c in flow.changed_functions}
    out = []
    for r, ref in cs.base.items():
        if cs.head[r].commit == ref.commit:
            continue
        src = git_source(ctx.cfg.repo(r), ctx.store.kb_home / "work" / ctx.cfg.project)
        for h in repo_diff(src, r, ref.commit, cs.head[r].commit, ctx.cfg.ignore, subpath=ctx.cfg.repo(r).path):
            if (r, h.file) in files or (r, h.old_file) in files:
                out.append(f"--- {r}:{h.file} @{h.new_start}\n" + "\n".join(
                    [f"- {x}" for x in h.removed] + [f"+ {x}" for x in h.added]))
    return "\n".join(out)


def _trace_path(ctx: _Ctx, flow_id: str, side: str) -> Path:
    return ctx.ps.dir(ctx.cfg.project, ctx.label, ctx.pid) / "traces" / f"{flow_id}-{side}-{ctx.commits}.json"


def _cached_trace(ctx: _Ctx, flow_id: str, side: str, make) -> Flow:
    p = _trace_path(ctx, flow_id, side)
    if p.exists():
        return Flow.model_validate_json(p.read_text())
    f = make()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f.model_dump_json())
    return f


def plan_work(state: DesignState, ctx: _Ctx, counts: dict, cap: int | None, only: set[str] | None = None,
              flow_ids: list[str] | None = None) -> dict:
    cs = ctx.ps.load_change_set(ctx.cfg.project, ctx.label, ctx.pid)
    impact = ctx.ps.load_impact(ctx.cfg.project, ctx.label, ctx.pid)
    if impact is None:
        raise KbError("this test plan has no impact yet: find what changed first")
    statuses = ("planned", "failed") if ctx.mode == "more" else ("planned",)
    slots = ctx.ps.load_slots(ctx.cfg.project, ctx.label, ctx.pid)
    if flow_ids is not None:
        slots = [s for s in slots if s.flow_id in set(flow_ids)]
    if only:
        chosen = [s for s in slots if s.id in only and s.status in ("planned", "failed", "stale")]
    else:
        chosen = select(slots, impact.flows, counts, cap, statuses)
    if not chosen:
        return {"work_ids": []}
    ids = {s.id for s in chosen}

    def reserve(all_slots):
        for s in all_slots:
            if s.id in ids:
                s.status, s.reserved_by, s.fail_reason = "reserved", ctx.author, None
    ctx.ps.update_slots(ctx.cfg.project, ctx.label, ctx.pid, reserve)
    ctx.reserved = ids
    ctx.commits = commits_key({r: h.commit for r, h in cs.head.items()})
    base_cos, head_cos = _checkouts(ctx, cs)
    pack = ctx.store.load(ctx.cfg.project, ctx.label)
    intent = "\n".join(f"{p.title}\n{p.body[:1500]}" for p in cs.prs.values()) or (cs.scenario_text or "")
    flows = {f.id: f for f in impact.flows}
    for s in chosen:
        ctx.chosen.setdefault(s.flow_id, []).append(s)
    for fid in ctx.chosen:
        f = flows[fid]
        ctx.works[fid] = DesignWork(flow=f, inventory=impact.inventory, base_pack=pack, store=ctx.store,
                                    base_cos=base_cos, head_cos=head_cos, provider=ctx.provider, tracer=ctx.tracer,
                                    mode="scenario" if cs.kind == "scenario" else "pr",
                                    changed_code=_changed_code(ctx, cs, f), intent=intent, author=ctx.author)
    ctx.total = len(ctx.works)
    return {"work_ids": list(ctx.works)}


def fan_out(state: DesignState):
    return [Send("flow", {"flow_id": fid}) for fid in state.get("work_ids", [])] or ["merge"]


def flow_node(state: DesignState, ctx: _Ctx) -> dict:
    fid = state["flow_id"]
    w, slots = ctx.works[fid], ctx.chosen[fid]
    project, label, pid = ctx.cfg.project, ctx.label, ctx.pid
    n = len(ctx.done) + 1
    try:
        with ctx.tracer.span(f"flow:{w.flow.name}"):
            ctx.report(f"Tracing {w.flow.name} on the {'PR branch' if w.mode == 'pr' else 'branch'} ({n}/{ctx.total})",
                       0.1 + 0.8 * (n - 1) / max(ctx.total, 1))
            if w.mode == "pr":
                w.base = _cached_trace(ctx, fid, "base", lambda: baseline_flow(w))
            w.head = _cached_trace(ctx, fid, "head", lambda: head_flow(w))
            w.diff = compare(w.base, w.head) if w.mode == "pr" else None
            ctx.report(f"Writing cases for {w.flow.name} ({n}/{ctx.total})", 0.1 + 0.8 * (n - 0.5) / max(ctx.total, 1))
            existing = ctx.ps.scenarios(project, label, pid)
            scen = write_scenarios(w, slots, existing)
            for s in scen:
                ctx.ps.save_scenario(project, label, pid, s)
            cases, extras = write_cases(w, scen, slots)
            kept, rejected = check_cases(cases, slots)
            ids = {}
            for c in kept:
                c.id = ctx.ps.next_case_id(project, label, pid)
                ids[c.slot_id] = c.id
                ctx.ps.save_case(project, label, pid, c)

            def apply(all_slots):
                for s in all_slots:
                    if s.id in ids:
                        s.status, s.case_id, s.reserved_by = "written", ids[s.id], None
                    elif s.id in rejected:
                        s.status, s.fail_reason, s.reserved_by = "planned", rejected[s.id], None
            ctx.ps.update_slots(project, label, pid, apply)
        ctx.done.append(fid)
        return {"results": [{"flow_id": fid, "written": len(kept), "failed": 0,
                             "extras": [e.model_dump(mode="json") for e in extras]}]}
    except Exception as e:  # noqa: BLE001 — one flow failing must not stop the others
        reason = str(e) or type(e).__name__
        mine = {s.id for s in slots}

        def fail(all_slots):
            for s in all_slots:
                if s.id in mine and s.status == "reserved":
                    s.status, s.fail_reason, s.reserved_by = "failed", reason, None
        ctx.ps.update_slots(project, label, pid, fail)
        ctx.done.append(fid)
        return {"results": [{"flow_id": fid, "written": 0, "failed": len(slots), "extras": [], "error": reason}]}


def merge(state: DesignState, ctx: _Ctx) -> dict:
    from kb.testplan.models import TestCase

    seen = {c.title.strip().lower() for c in ctx.ps.cases(ctx.cfg.project, ctx.label, ctx.pid) if c.suggested}
    n_extras = 0
    for r in state.get("results", []):
        for e in r.get("extras", []):
            key = e["title"].strip().lower()
            if key in seen:
                continue
            seen.add(key)
            c = TestCase.model_validate(e)
            c.id = ctx.ps.next_case_id(ctx.cfg.project, ctx.label, ctx.pid)
            ctx.ps.save_case(ctx.cfg.project, ctx.label, ctx.pid, c)
            n_extras += 1
    res = DesignResult(written=sum(r["written"] for r in state.get("results", [])),
                       failed=sum(r["failed"] for r in state.get("results", [])), extras=n_extras)
    return {"result": res}


def coverage_for(ps: PlanStore, project: str, label: str, pid: str) -> Coverage:
    impact = ps.load_impact(project, label, pid)
    slots = ps.load_slots(project, label, pid)
    cov = Coverage()
    for item in (impact.inventory if impact else []):
        mine = [s for s in slots if item.id in s.inventory_ids]
        st = {s.status for s in mine}
        cov.items[item.id] = ("covered" if st & {"written", "stale"} else "failed" if "failed" in st
                              else "skipped" if mine else "uncovered")
    for s in slots:
        cov.by_status[s.status] = cov.by_status.get(s.status, 0) + 1
        if s.status == "written":
            cov.by_priority[s.priority] = cov.by_priority.get(s.priority, 0) + 1
    cov.by_priority = {p: cov.by_priority[p] for p in PRIORITIES if p in cov.by_priority}
    return cov


def coverage_node(state: DesignState, ctx: _Ctx) -> dict:
    res = state["result"]
    res.coverage = coverage_for(ctx.ps, ctx.cfg.project, ctx.label, ctx.pid)
    return {"result": res}


def run_design(cfg, plan_id: str, label: str, store, provider, tracer, mode: Mode, counts: dict[str, int],
               per_flow_cap: int | None, progress=None, author: str = "unknown", only: set[str] | None = None,
               flow_ids: list[str] | None = None) -> DesignResult:
    ctx = _Ctx(cfg=cfg, store=store, ps=PlanStore(store), pid=plan_id, label=label, mode=mode, provider=provider,
               tracer=tracer, author=author, report=progress or (lambda text, frac: None))
    provider.check()
    g = StateGraph(DesignState)
    g.add_node("plan_work", lambda s: plan_work(s, ctx, counts, per_flow_cap, only, flow_ids))
    g.add_node("flow", lambda s: flow_node(s, ctx))
    g.add_node("merge", lambda s: merge(s, ctx))
    g.add_node("coverage", lambda s: coverage_node(s, ctx))
    g.add_edge(START, "plan_work")
    g.add_conditional_edges("plan_work", fan_out, ["flow", "merge"])
    g.add_edge("flow", "merge")
    g.add_edge("merge", "coverage")
    g.add_edge("coverage", END)
    def release(only_ids=None):
        def fn(slots):
            for s in slots:
                if s.status == "reserved" and (only_ids is None or s.id in only_ids):
                    s.status, s.reserved_by = "planned", None
        ctx.ps.update_slots(cfg.project, label, plan_id, fn)

    # One design job per plan runs at a time (the job key), so anything still "reserved" is left over from
    # a run that died (engine killed, app quit): give it back.
    release()
    try:
        with tracer.trace("testplan_design", {"project": cfg.project, "plan": plan_id, "mode": mode}) as root:
            state = g.compile().invoke({"results": []}, config={"callbacks": tracer.langgraph_callbacks()})
            res = state["result"]
            root.end(output=res.model_dump())
    except BaseException:
        release(ctx.reserved)
        raise
    tracer.flush()
    ctx.report("Done", 1.0)
    return res
