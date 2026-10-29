"""Feature A — Test Scenarios in a Branch: plan test cases for how one flow works today, from a scenario
text or a saved answer. Slots are planned from the decision points in the code the flow cites."""

from __future__ import annotations

import hashlib
from collections import Counter

from pydantic import BaseModel

from kb.errors import KbError
from kb.packs.model import Flow, commits_key
from kb.testplan.delta import symbol_spans
from kb.testplan.inventory import _id, _summary, extract_decisions
from kb.testplan.models import BranchRef, CaseSlot, ChangeSet, ImpactFlow, ImpactReport, InventoryItem
from kb.testplan.slots import assign_priorities, plan_slots, risk
from kb.testplan.nodes_impact import reconcile
from kb.testplan.store import PlanStore


def innermost(spans: list[tuple[str, int, int]], start: int, end: int) -> list[tuple[str, int, int]]:
    """Symbols overlapping lines [start, end], dropping any that contains another overlapping one (a class
    whose method is cited), so each decision point is listed once, under its method."""
    hit = [s for s in spans if s[1] <= end and start <= s[2]]
    return [s for s in hit if not any(o is not s and s[1] <= o[1] and o[2] <= s[2] and (o[1], o[2]) != (s[1], s[2])
                                      for o in hit)]


class ScenarioPlan(BaseModel):
    plan_id: str
    flow: ImpactFlow
    slots: list[CaseSlot]
    counts: dict[str, int]


def _flow_for(cfg, label, store, provider, tracer, text, answer_id, author) -> tuple[Flow, str]:
    pack = store.load(cfg.project, label)
    if answer_id:
        qa = next((q for q in pack.qa if q.id == answer_id), None)
        if qa is None:
            raise KbError(f"no saved answer {answer_id} on {cfg.project}@{label}")
        flow = next((f for f in pack.flows if f.id == qa.flow_id), None)
        if flow is None:
            raise KbError("that answer has no traced flow to plan tests from; ask it again first")
        return flow, qa.question
    if not (text or "").strip():
        raise KbError("describe the scenario to test")
    from kb.ask import run_ask

    st = run_ask(cfg, label, text.strip(), store, provider, tracer, author=author)
    if st.get("not_found"):
        raise KbError("couldn't find that scenario in this code; try naming the screen or feature")
    pack = store.load(cfg.project, label)
    fid = st.get("reused_flow_id") or next((q.flow_id for q in pack.qa if q.id == st.get("saved_qa_id")), None)
    flow = next((f for f in pack.flows if f.id == fid), None)
    if flow is None:
        raise KbError("the scenario was answered without a traced flow; try a more specific scenario")
    return flow, text.strip()


def plan_scenario(cfg, label: str, store, provider, tracer, text: str | None = None, answer_id: str | None = None,
                  author: str = "unknown") -> ScenarioPlan:
    flow, title = _flow_for(cfg, label, store, provider, tracer, text, answer_id, author)
    pack = store.load(cfg.project, label)
    from kb.ask import available_checkouts

    cos, _ = available_checkouts(cfg, label, store, pack.manifest.commits, pack.manifest.branches)
    items: dict[str, InventoryItem] = {}
    functions: list[str] = []
    hashes: dict[str, str] = {}  # each function's source, so re-planning after a code change marks cases stale
    for step in flow.steps:
        c = step.citation
        co = cos.get(c.repo) if c else None
        if co is None:
            continue
        try:
            src = (co.root / c.file).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name, a, b in innermost(symbol_spans(src, c.file), c.line_start, c.line_end):
            fid = f"{c.repo}:{c.file}#{name}"
            if fid in functions:
                continue
            functions.append(fid)
            hashes[fid] = hashlib.sha1("\n".join(src.splitlines()[a - 1:b]).encode()).hexdigest()[:12]
            for d in extract_decisions(src, c.file, a, b):
                iid = _id(c.repo, c.file, name, d.kind, d.norm)
                items.setdefault(iid, InventoryItem(id=iid, kind=d.kind, change="added", repo=c.repo, file=c.file,
                                                    lines=(d.line, d.line), function=name, outcomes=d.outcomes,
                                                    summary=_summary(d.kind, d), text=d.norm))
    key = answer_id or (text or "").strip().lower()
    pid = "sc-" + hashlib.sha1(f"{cfg.project}|{label}|{key}".encode()).hexdigest()[:10]
    repos = {f.split(":", 1)[0] for f in functions}
    imp_flow = ImpactFlow(id=hashlib.sha1(f"{pid}|flow".encode()).hexdigest()[:12], name=title,
                          entry_points=[functions[0]] if functions else [], changed_functions=functions,
                          inventory_ids=list(items), modules=list(flow.module_ids), master_flow_id=flow.id,
                          crosses_repos=len(repos) > 1)
    inv = list(items.values())
    imp_flow.risk, imp_flow.risk_reasons = risk(imp_flow, inv)
    slots = plan_slots([imp_flow], inv, "scenario")
    assign_priorities(slots, [imp_flow], inv)

    ps = PlanStore(store)
    refs = {r: BranchRef(branch=(pack.manifest.branches or {}).get(r, label), commit=c)
            for r, c in pack.manifest.commits.items()}
    cs = ChangeSet(id=pid, kind="scenario", project=cfg.project, title=title, base=refs, head=refs, base_pack=label,
                   scenario_text=text, answer_id=answer_id, created_by=author)
    ps.save_change_set(label, cs)
    report = ImpactReport(change_set_id=pid, head_commits=dict(pack.manifest.commits), inventory=inv, flows=[imp_flow],
                          graph_delta={"function_hashes": hashes}, created_by=author)
    previous = ps.load_impact(cfg.project, label, pid)
    # re-planning keeps what was written: edited code → stale, a decision point that's gone → obsolete
    reconcile(ps, cfg.project, label, pid, slots, previous, report)
    ps.save_impact(cfg.project, label, report)
    slots = ps.load_slots(cfg.project, label, pid)
    # the flow is already traced: reuse it as the plan's head trace
    tp = ps.dir(cfg.project, label, pid) / "traces" / f"{imp_flow.id}-head-{commits_key({r: b.commit for r, b in refs.items()})}.json"
    tp.parent.mkdir(parents=True, exist_ok=True)
    tp.write_text(flow.model_dump_json())
    return ScenarioPlan(plan_id=pid, flow=imp_flow, slots=slots, counts=dict(Counter(s.priority for s in slots if s.status != "obsolete")))
