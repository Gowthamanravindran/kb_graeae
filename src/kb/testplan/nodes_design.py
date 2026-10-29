"""Per-flow nodes of the Design graph (spec §3.2): trace the flow on base and PR code (reusing the Ask agent's
collect → trace → verify), compare, then write scenarios and exactly one case per slot."""

from __future__ import annotations

import difflib
import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from kb.agent import nodes as agent
from kb.agent.graph import AgentDeps
from kb.llm.base import LLMRequest
from kb.llm.json_call import ask_json
from kb.packs.model import Flow
from kb.testplan.models import CaseSlot, ImpactFlow, InventoryItem, Scenario, TestCase
from kb.testplan.slots import CASES_PER_CALL

TYPE_TITLES = {"happy_path": "Main path works", "branches_edges": "Branches and edge cases",
               "errors_failures": "Errors and failures", "regression": "What used to work still works",
               "cross_side": "Effects on the other side"}
QA_SYSTEM = ("You write manual test cases for a QA team. Plain English a tester can follow without reading code: "
             "concrete preconditions, numbered actions in the UI or API, realistic test data, and observable "
             "expected results. Ground every case in the flow and decision points you are given; do not invent "
             "features.")


@dataclass
class DesignWork:
    flow: ImpactFlow
    inventory: list[InventoryItem]
    base_pack: object
    store: object
    base_cos: dict
    head_cos: dict
    provider: object
    tracer: object
    mode: str = "pr"
    changed_code: str = ""
    intent: str = ""
    author: str = "unknown"
    base: Flow | None = None
    head: Flow | None = None
    diff: "FlowDiff | None" = None
    extra: dict = field(default_factory=dict)


class FlowDiff(BaseModel):
    added: list[int] = Field(default_factory=list)      # head step indexes
    removed: list[int] = Field(default_factory=list)    # base step indexes
    changed: list[int] = Field(default_factory=list)    # head step indexes
    unchanged: list[int] = Field(default_factory=list)  # head step indexes


def trace_flow(work: DesignWork, checkouts: dict, context: str) -> Flow:
    deps = AgentDeps(pack=work.base_pack, pack_dir=work.base_pack.dir, checkouts=checkouts, provider=work.provider,
                     tracer=work.tracer, store=work.store, author=work.author, context="",
                     extra={"trace_context": context})
    state = {"question": f"How does: {work.flow.name}", "module_ids": list(work.flow.modules)}
    state.update(agent.collect(state, deps) or {})
    state.update(agent.trace(state, deps) or {})
    state.update(agent.verify(state, deps) or {})
    steps = state.get("steps", [])
    return Flow(id=f"tp-{uuid.uuid4().hex[:10]}", question=state["question"], module_ids=list(work.flow.modules),
                steps=steps, mermaid=agent.mermaid_for(steps) if steps else "", created_at=datetime.now(timezone.utc))


def _items(work: DesignWork) -> list[InventoryItem]:
    ids = set(work.flow.inventory_ids)
    return [i for i in work.inventory if i.id in ids]


def baseline_flow(work: DesignWork) -> Flow:
    if work.flow.master_flow_id:
        from kb.packs.staleness import is_stale

        f = next((x for x in work.base_pack.flows if x.id == work.flow.master_flow_id), None)
        if f is not None and not any(s.citation and is_stale(s.citation, work.base_cos) for s in f.steps):
            return f
    ctx = ("This is the CURRENT PRODUCTION behaviour (the base branch). Entry point: "
           + ", ".join(work.flow.entry_points))
    return trace_flow(work, work.base_cos, ctx)


def head_flow(work: DesignWork) -> Flow:
    items = _items(work)
    ctx = ["CHANGED CODE in this pull request (trace the flow as it is on the PR branch):", work.changed_code[:12000]]
    if work.intent:
        ctx.append("What the PR says it does:\n" + work.intent[:3000])
    ctx.append("Entry point: " + ", ".join(work.flow.entry_points))
    if items:
        ctx.append("Include a step for each of these decision points:\n" + "\n".join(f"- {i.summary}" for i in items))
    return trace_flow(work, work.head_cos, "\n\n".join(ctx))


def _loc(s):
    c = s.citation
    return (c.repo, c.file, c.line_start, c.line_end) if c else None


def compare(base: Flow | None, head: Flow) -> FlowDiff:
    d = FlowDiff()
    if base is None:
        d.added = list(range(len(head.steps)))
        return d
    used = set()
    for hi, h in enumerate(head.steps):
        match = None
        hl = _loc(h)
        for bi, b in enumerate(base.steps):
            bl = _loc(b)
            if bi in used or not hl or not bl or hl[:2] != bl[:2]:
                continue
            overlap = not (hl[3] < bl[2] or hl[2] > bl[3])
            if overlap and difflib.SequenceMatcher(None, b.text.lower(), h.text.lower()).ratio() >= 0.4:
                match = bi  # same code and a similar description
                break
        if match is None:
            best, score = None, 0.0
            for bi, b in enumerate(base.steps):
                if bi in used:
                    continue
                r = difflib.SequenceMatcher(None, b.text.lower(), h.text.lower()).ratio()
                if r > score:
                    best, score = bi, r
            match = best if score >= 0.6 else None
        if match is None:
            d.added.append(hi)
            continue
        used.add(match)
        (d.unchanged if base.steps[match].text.strip().lower() == h.text.strip().lower() else d.changed).append(hi)
    d.removed = [bi for bi in range(len(base.steps)) if bi not in used]
    return d


def _flow_text(f: Flow | None, title: str) -> str:
    if f is None:
        return ""
    lines = [f"{title}:"]
    for i, s in enumerate(f.steps):
        c = s.citation
        lines.append(f"{i}. [{s.side}] {s.text}" + (f" ({c.repo}:{c.file}:{c.line_start})" if c else "")
                     + ("" if s.verified else " [unverified]"))
    return "\n".join(lines)


def _context(work: DesignWork) -> str:
    parts = [f"Flow: {work.flow.name}", f"Entry point: {', '.join(work.flow.entry_points)}"]
    if work.intent:
        parts.append("Intent of the change:\n" + work.intent[:2000])
    parts.append(_flow_text(work.head, "Flow steps (as it works now)" if work.mode == "scenario" else "Flow steps on the PR branch"))
    if work.mode == "pr" and work.base is not None:
        parts.append(_flow_text(work.base, "Flow steps in production (base branch)"))
        if work.diff is not None:
            parts.append(f"Step comparison: added {work.diff.added}, changed {work.diff.changed}, "
                         f"removed (base) {work.diff.removed}, unchanged {work.diff.unchanged}")
    items = _items(work)
    if items:
        parts.append("Decision points:\n" + "\n".join(f"- {i.id}: {i.summary} (outcomes: {', '.join(i.outcomes)})" for i in items))
    return "\n\n".join(p for p in parts if p)


def _roots(work: DesignWork) -> tuple[Path, list[Path]]:
    roots = [Path(c.root) for c in (work.head_cos or work.base_cos).values()]
    return (roots[0] if roots else Path(work.base_pack.dir)), roots[1:]


class _Scen(BaseModel):
    type: str
    title: str
    description: str = ""
    inventory_ids: list[str] = Field(default_factory=list)
    step_refs: list[int] = Field(default_factory=list)


class ScenariosOut(BaseModel):
    scenarios: list[_Scen]


def _scenario_id(flow_id: str, typ: str) -> str:
    return hashlib.sha1(f"{flow_id}|{typ}".encode()).hexdigest()[:12]


def write_scenarios(work: DesignWork, slots: list[CaseSlot], existing: list[Scenario] | None = None) -> list[Scenario]:
    have = {s.type: s for s in (existing or []) if s.flow_id == work.flow.id}
    needed = sorted({s.scenario_type for s in slots} - set(have))
    out = [have[t] for t in sorted(have) if t in {s.scenario_type for s in slots}]
    if not needed:
        return out
    prompt = (_context(work) + "\n\nWrite one test scenario for each of these types: " + ", ".join(needed)
              + ". A scenario is a short title (what is being tested) and a one or two sentence description."
              + ' Return JSON: {"scenarios": [{"type": "...", "title": "...", "description": "...", '
              '"inventory_ids": ["..."], "step_refs": [0]}]}')
    cwd, extra = _roots(work)
    got = ask_json(work.provider, LLMRequest(prompt=prompt, system=QA_SYSTEM, cwd=cwd, extra_dirs=extra, allow_tools=False),
                   ScenariosOut, work.tracer, "scenarios")
    by_type = {}
    for s in got.scenarios:
        if s.type in needed and s.type not in by_type:
            by_type[s.type] = s
    for t in needed:
        s = by_type.get(t)
        out.append(Scenario(id=_scenario_id(work.flow.id, t), flow_id=work.flow.id, type=t,
                            title=(s.title if s else f"{TYPE_TITLES[t]}: {work.flow.name}"),
                            description=s.description if s else "", inventory_ids=s.inventory_ids if s else [],
                            step_refs=s.step_refs if s else [], author=work.author))
    return out


class _Case(BaseModel):
    slot_id: str | None = None
    title: str
    preconditions: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    test_data: list[str] = Field(default_factory=list)
    expected: list[str] = Field(default_factory=list)


class CasesOut(BaseModel):
    cases: list[_Case]
    extras: list[_Case] = Field(default_factory=list)


def write_cases(work: DesignWork, scenarios: list[Scenario], slots: list[CaseSlot]
                ) -> tuple[list[TestCase], list[TestCase], dict[str, str]]:
    """One Claude call per batch of ≤15 slots; returns (cases with slot ids, suggested extras, {slot id: reason}
    for batches that failed). A failed batch doesn't throw away the ones that worked; if none worked, it raises."""
    inv = {i.id: i for i in work.inventory}
    scen_by_type = {s.type: s for s in scenarios}
    cwd, extra = _roots(work)
    written, extras, failed = [], [], {}
    last_error: Exception | None = None
    for i in range(0, len(slots), CASES_PER_CALL):
        batch = slots[i:i + CASES_PER_CALL]
        lines = []
        for s in batch:
            what = "; ".join(inv[x].summary for x in s.inventory_ids if x in inv) or s.rule or s.scenario_type
            sc = scen_by_type.get(s.scenario_type)
            lines.append(f"- slot {s.id} | {s.priority} | {s.scenario_type}"
                         + (f" ({sc.title})" if sc else "") + f" | tests: {what}" + (f" | outcome: {s.outcome}" if s.outcome else ""))
        prompt = (_context(work) + "\n\nWrite exactly one test case for each slot below, and keep its slot id:\n"
                  + "\n".join(lines)
                  + "\n\nIf you see something important to test that no slot covers, add it to \"extras\" (no slot id)."
                  + ' Return JSON: {"cases": [{"slot_id": "...", "title": "...", "preconditions": ["..."], '
                    '"steps": ["..."], "test_data": ["..."], "expected": ["..."]}], "extras": []}')
        try:
            got = ask_json(work.provider, LLMRequest(prompt=prompt, system=QA_SYSTEM, cwd=cwd, extra_dirs=extra,
                                                     allow_tools=False), CasesOut, work.tracer, "cases")
        except Exception as e:  # noqa: BLE001 — this batch's slots fail; earlier and later batches stand
            last_error = e
            failed.update({s.id: str(e) or type(e).__name__ for s in batch})
            continue
        prio = {s.id: s.priority for s in batch}
        typ = {s.id: s.scenario_type for s in batch}
        refs = [st.citation for st in (work.head.steps if work.head else []) if st.citation]
        for c in got.cases:
            sid = c.slot_id or ""
            sc = scen_by_type.get(typ.get(sid, ""))
            written.append(TestCase(id="", slot_id=sid, scenario_id=sc.id if sc else None, flow_id=work.flow.id,
                                    title=c.title, priority=prio.get(sid, "P2"), preconditions=c.preconditions,
                                    steps=c.steps, test_data=c.test_data, expected=c.expected,
                                    code_refs=refs[:6], author=work.author))
        for c in got.extras:
            extras.append(TestCase(id="", suggested=True, status="suggested", flow_id=work.flow.id, title=c.title,
                                   priority="P2", preconditions=c.preconditions, steps=c.steps,
                                   test_data=c.test_data, expected=c.expected, author=work.author))
    if last_error is not None and len(failed) == len(slots):
        raise last_error
    return written, extras, failed


def check_cases(cases: list[TestCase], slots: list[CaseSlot]) -> tuple[list[TestCase], dict[str, str]]:
    """Keep one grounded case per given slot. Unknown slot ids are dropped; slots without a usable case are
    returned with the reason (they go back to `planned`)."""
    given = {s.id for s in slots}
    kept, rejected, seen = [], {}, set()
    for c in cases:
        if c.slot_id not in given or c.slot_id in seen:
            continue
        if not c.steps or not c.expected:
            rejected[c.slot_id] = "missing steps or expected result"
            continue
        seen.add(c.slot_id)
        kept.append(c)
    for s in slots:
        if s.id not in seen and s.id not in rejected:
            rejected[s.id] = "not returned"
    return kept, rejected
