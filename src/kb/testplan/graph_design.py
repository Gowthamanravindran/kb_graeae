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
    extras_from_conflicts: int = 0  # cases a teammate had already written (kept as suggested extras)
    warnings: list[str] = Field(default_factory=list)
    coverage: Coverage = Field(default_factory=Coverage)


OFFLINE = "Offline: these cases are not reserved and may duplicate a teammate's"
RENEW_EVERY = 600.0  # seconds: reservations and trace locks are renewed this often while a run lasts


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
    client: Any = None          # a RegistryClient when the plan is shared with the team
    shared: bool = False
    offline: bool = False
    traces_held: set = field(default_factory=set)  # (flow id, side)
    conflicts: int = 0


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
    from kb.testplan.worktrees import diff_source

    files = {(c.split(":", 1)[0], c.split(":", 1)[1].split("#", 1)[0]) for c in flow.changed_functions}
    out = []
    for r, ref in cs.base.items():
        if cs.head[r].commit == ref.commit:
            continue
        src = diff_source(ctx.cfg.repo(r), ctx.store.kb_home / "work" / ctx.cfg.project, ref.commit, cs.head[r].commit)
        for h in repo_diff(src, r, ref.commit, cs.head[r].commit, ctx.cfg.ignore, subpath=ctx.cfg.repo(r).path):
            if (r, h.file) in files or (r, h.old_file) in files:
                out.append(f"--- {r}:{h.file} @{h.new_start}\n" + "\n".join(
                    [f"- {x}" for x in h.removed] + [f"+ {x}" for x in h.added]))
    return "\n".join(out)


def _trace_path(ctx: _Ctx, flow_id: str, side: str) -> Path:
    return ctx.ps.dir(ctx.cfg.project, ctx.label, ctx.pid) / "traces" / f"{flow_id}-{side}-{ctx.commits}.json"


def _cached_trace(ctx: _Ctx, flow_id: str, side: str, make) -> Flow:
    """A trace is made once per plan and commits. On a shared plan a teammate's trace (pulled with the plan) is
    reused; otherwise this run takes the flow's trace lock first, so two people never pay for the same trace."""
    from kb.sync.client import TraceBusy
    from kb.errors import RegistryUnavailable

    p = _trace_path(ctx, flow_id, side)
    if p.exists():
        return Flow.model_validate_json(p.read_text())
    online = ctx.shared and not ctx.offline
    if online:
        try:
            ctx.client.lock_trace(ctx.cfg.project, ctx.label, ctx.pid, flow_id, side)
            ctx.traces_held.add((flow_id, side))
        except TraceBusy as e:
            name = next((w.flow.name for w in ctx.works.values() if w.flow.id == flow_id), flow_id)
            raise KbError(f"{e.holder} is tracing {name}; try again in a few minutes") from e
        except RegistryUnavailable:
            online, ctx.offline = False, True  # carry on offline: cases are queued as unreserved
    try:
        f = make()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f.model_dump_json())
        if ctx.shared:
            from kb.testplan import shared

            shared.publish_traces_and_scenarios(ctx.cfg, ctx.label, ctx.pid, ctx.store, ctx.client, {p.stem: f}, [])
        return f
    finally:  # traced or failed, the lock goes back so a teammate isn't told to wait for a trace that won't come
        if online:
            _unlock(ctx, flow_id, side)


def _unlock(ctx: _Ctx, flow_id: str, side: str) -> None:
    from kb.errors import RegistryUnavailable

    try:
        ctx.client.unlock_trace(ctx.cfg.project, ctx.label, ctx.pid, flow_id, side)
    except (RegistryUnavailable, KbError):
        pass
    ctx.traces_held.discard((flow_id, side))


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
    if ctx.shared and not ctx.offline:
        chosen = _reserve_shared(ctx, chosen, counts, cap, only, flow_ids, statuses)
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


def _reserve_shared(ctx: _Ctx, local_choice, counts, cap, only, flow_ids, statuses) -> list[CaseSlot]:
    """Ask the registry for the slots (it hands out each slot to one person only). Offline: fall back to the local
    choice, warn, and write the cases as unreserved."""
    from kb.errors import RegistryUnavailable
    from kb.testplan import shared

    try:
        if only:
            granted = shared.reserve(ctx.cfg, ctx.label, ctx.pid, ctx.store, ctx.client, slot_ids=sorted(only))
        else:
            granted = shared.reserve(ctx.cfg, ctx.label, ctx.pid, ctx.store, ctx.client,
                                     counts={k: v for k, v in counts.items() if v}, per_flow_cap=cap, flow_ids=flow_ids)
    except RegistryUnavailable:
        ctx.offline = True
        return local_choice
    by_id = {s.id: s for s in ctx.ps.load_slots(ctx.cfg.project, ctx.label, ctx.pid)}
    return [by_id[i] for i in granted if i in by_id]


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
            if ctx.shared:
                from kb.testplan import shared

                shared.publish_traces_and_scenarios(ctx.cfg, label, pid, ctx.store, ctx.client, {}, scen)
            cases, extras, failed = write_cases(w, scen, slots)
            kept, rejected = check_cases(cases, [s for s in slots if s.id not in failed])
            ids = {}
            new_ids = _case_ids(ctx, len(kept))
            for c, cid in zip(kept, new_ids):
                c.id = cid
                ids[c.slot_id] = c.id
                ctx.ps.save_case(project, label, pid, c)

            orphans: list[str] = []

            def apply(all_slots):
                present = {s.id for s in all_slots}
                # a re-run of the impact can drop a slot while its case was being written: keep the case, obsolete
                orphans[:] = [cid for sid, cid in ids.items() if sid not in present]
                for s in all_slots:
                    if s.id in ids:
                        s.status, s.case_id, s.reserved_by = "written", ids[s.id], None
                    elif s.id in rejected:
                        s.status, s.fail_reason, s.reserved_by = "planned", rejected[s.id], None
                    elif s.id in failed and s.status == "reserved":
                        s.status, s.fail_reason, s.reserved_by = "failed", failed[s.id], None
            ctx.ps.update_slots(project, label, pid, apply)
            for c in kept:
                if c.id in orphans:
                    ctx.ps.save_case(project, label, pid, c.model_copy(update={"status": "obsolete"}))
            if ctx.shared:
                from kb.testplan import shared

                live = [c for c in kept if c.id not in orphans]
                res = shared.publish_cases(ctx.cfg, label, pid, live, ctx.store, ctx.client)
                ctx.conflicts += len(res.extras)
                orphans += res.extras  # a teammate wrote these slots first: they became suggested extras
        ctx.done.append(fid)
        return {"results": [{"flow_id": fid, "written": len(kept) - len(orphans), "failed": len(failed),
                             "extras": [e.model_dump(mode="json") for e in extras]}]}
    except Exception as e:  # noqa: BLE001 — one flow failing must not stop the others
        reason = str(e) or type(e).__name__
        mine = {s.id for s in slots}

        def fail(all_slots):
            for s in all_slots:
                if s.id in mine and s.status == "reserved":
                    s.status, s.fail_reason, s.reserved_by = "failed", reason, None
        ctx.ps.update_slots(project, label, pid, fail)
        if ctx.shared and not ctx.offline:
            _give_back(ctx, mine)
        ctx.done.append(fid)
        return {"results": [{"flow_id": fid, "written": 0, "failed": len(slots), "extras": [], "error": reason}]}


def _case_ids(ctx: _Ctx, n: int) -> list[str]:
    if ctx.shared:
        from kb.testplan import shared

        return shared.new_case_ids(ctx.cfg, ctx.label, ctx.pid, ctx.client, n)
    return [ctx.ps.next_case_id(ctx.cfg.project, ctx.label, ctx.pid) for _ in range(n)]


def _reclaim(ctx: _Ctx) -> None:
    """Cases still reserved by me when a run starts were left by a run that died (one design job per plan runs at
    a time here): give them back, so this run can take them again instead of waiting 24 hours."""
    from kb.errors import RegistryUnavailable

    try:
        me = ctx.client.me().get("email")
    except (RegistryUnavailable, KbError):
        return
    mine = [s.id for s in ctx.ps.load_slots(ctx.cfg.project, ctx.label, ctx.pid) if s.status == "reserved" and s.reserved_by == me]
    if mine:
        _give_back(ctx, mine)
        from kb.testplan import shared

        shared.sync(ctx.cfg, ctx.label, ctx.pid, ctx.store, ctx.client)


def _give_back(ctx: _Ctx, slot_ids) -> None:
    from kb.errors import RegistryUnavailable

    if slot_ids:
        try:
            ctx.client.release(ctx.cfg.project, ctx.label, ctx.pid, sorted(slot_ids))
        except RegistryUnavailable:
            pass


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
            c.id = _case_ids(ctx, 1)[0]
            ctx.ps.save_case(ctx.cfg.project, ctx.label, ctx.pid, c)
            if ctx.shared:
                from kb.testplan import shared

                shared.publish_cases(ctx.cfg, ctx.label, ctx.pid, [c], ctx.store, ctx.client)
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


class _Renewer:
    """Keeps a run's reservations and trace locks alive (renewed every RENEW_EVERY seconds) until it ends."""

    def __init__(self, ctx: _Ctx):
        import threading

        self.ctx, self.done = ctx, threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self) -> None:
        from kb.errors import KbError as _E

        ctx = self.ctx
        while not self.done.wait(RENEW_EVERY):
            try:
                if ctx.reserved and not ctx.offline:
                    ctx.client.renew(ctx.cfg.project, ctx.label, ctx.pid, sorted(ctx.reserved))
                for flow_id, side in list(ctx.traces_held):
                    ctx.client.lock_trace(ctx.cfg.project, ctx.label, ctx.pid, flow_id, side)
            except _E:
                pass  # offline or busy: the next round tries again

    def stop(self) -> None:
        self.done.set()
        self.thread.join(timeout=5)


def run_design(cfg, plan_id: str, label: str, store, provider, tracer, mode: Mode, counts: dict[str, int],
               per_flow_cap: int | None, progress=None, author: str = "unknown", only: set[str] | None = None,
               flow_ids: list[str] | None = None, client=None) -> DesignResult:
    ctx = _Ctx(cfg=cfg, store=store, ps=PlanStore(store), pid=plan_id, label=label, mode=mode, provider=provider,
               tracer=tracer, author=author, report=progress or (lambda text, frac: None))
    ctx.client = client
    ctx.shared = client is not None and ctx.ps.load_change_set(cfg.project, label, plan_id).shared
    provider.check()
    if ctx.shared:
        from kb.testplan import shared

        ctx.report("Syncing with the team", 0.02)
        ctx.offline = shared.sync(cfg, label, plan_id, store, client).offline
        if not ctx.offline:
            _reclaim(ctx)
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
    # a run that died (engine killed, app quit): give it back. On a shared plan "reserved" is the registry's
    # view (teammates' work too), so the registry's expiry does this instead.
    if not ctx.shared:
        release()
    renewer = _Renewer(ctx) if ctx.shared else None
    try:
        with tracer.trace("testplan_design", {"project": cfg.project, "plan": plan_id, "mode": mode}) as root:
            state = g.compile().invoke({"results": []}, config={"callbacks": tracer.langgraph_callbacks()})
            res = state["result"]
            root.end(output=res.model_dump())
    except BaseException:
        release(ctx.reserved)
        if ctx.shared and not ctx.offline:
            _give_back(ctx, ctx.reserved)
        raise
    finally:
        if renewer is not None:
            renewer.stop()
        for flow_id, side in list(ctx.traces_held):
            _unlock(ctx, flow_id, side)
    if ctx.shared:
        res.extras_from_conflicts = ctx.conflicts
        written = {s.id for s in ctx.ps.load_slots(cfg.project, label, plan_id) if s.status == "written"}
        if not ctx.offline:
            _give_back(ctx, ctx.reserved - written)
        if ctx.offline:
            res.warnings.append(OFFLINE)
        from kb.testplan import shared

        shared.sync(cfg, label, plan_id, store, client)
        res.coverage = coverage_for(ctx.ps, cfg.project, label, plan_id)
    tracer.flush()
    ctx.report("Done", 1.0)
    return res
