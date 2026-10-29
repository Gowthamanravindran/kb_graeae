import json
from datetime import datetime, timezone

from kb.packs.model import Citation, Flow, FlowStep
from kb.testplan.models import CaseSlot, ImpactFlow, InventoryItem, Scenario
from kb.testplan.nodes_design import (DesignWork, baseline_flow, check_cases, compare, head_flow, write_cases,
                                      write_scenarios)

from agent_helpers import GOOD_STEPS, build_pack, save_flow, trace_reply
from fakes import FakeProvider, RecordingTracer


def st(text, file="a.ts", line=1, side="backend"):
    return FlowStep(side=side, text=text, citation=Citation(repo="be", file=file, line_start=line, line_end=line + 2, content_hash="h"),
                    verified=True)


def F(steps):
    return Flow(id="x", question="q", module_ids=[], steps=steps, mermaid="", created_at=datetime.now(timezone.utc))


def test_compare_marks_added_removed_unchanged():
    base = F([st("Form submits", "f.tsx", 5), st("Saves the task", "r.ts", 6), st("Notifies the assignee", "r.ts", 30)])
    head = F([st("Form submits", "f.tsx", 5), st("Checks the user is an admin", "r.ts", 7), st("Saves the task with end date", "r.ts", 14)])
    d = compare(base, head)
    assert d.unchanged == [0] and d.removed == [2]
    assert 1 in d.added and 2 in d.changed


SLOTS = [CaseSlot(id=s, flow_id="fl", scenario_type=t, priority=p, inventory_ids=["inv1"], outcome=o)
         for s, t, p, o in [("s1", "branches_edges", "P0", "allowed"), ("s2", "errors_failures", "P0", "denied"),
                            ("s3", "happy_path", "P1", "")]]
INV = [InventoryItem(id="inv1", kind="permission", change="added", repo="mini-be", file="r.ts", lines=(7, 7),
                     function="AddTask", summary="Permission check `!context.user.isAdmin`", outcomes=["allowed", "denied"])]
FLOW = ImpactFlow(id="fl", name="Admin creates a task", entry_points=["e"], changed_functions=["mini-be:r.ts#AddTask"],
                  inventory_ids=["inv1"], modules=["tasks"])


def _work(tmp_path, provider):
    store, cos = build_pack(tmp_path)
    return DesignWork(flow=FLOW, inventory=INV, base_pack=store.load("mini", "main"), store=store,
                      base_cos=cos, head_cos=cos, provider=provider, tracer=RecordingTracer(), mode="pr",
                      changed_code="+ if (!context.user.isAdmin) {", intent="PR: admin only")


def test_baseline_reuses_saved_flow(tmp_path):
    p = FakeProvider()
    w = _work(tmp_path, p)
    save_flow(w.store, w.base_cos)
    w.base_pack = w.store.load("mini", "main")
    w.flow = FLOW.model_copy(update={"master_flow_id": "f1"})
    assert baseline_flow(w).id == "f1" and p.requests == []


def test_head_prompt_lists_decision_points(tmp_path):
    p = FakeProvider([trace_reply(GOOD_STEPS)])
    w = _work(tmp_path, p)
    flow = head_flow(w)
    prompt = p.requests[0].prompt
    assert "CHANGED CODE" in prompt and "Permission check `!context.user.isAdmin`" in prompt
    assert "+ if (!context.user.isAdmin) {" in prompt
    assert len(flow.steps) == 3


def _cases_reply(slot_ids, extras=()):
    cases = [{"slot_id": s, "title": f"Case {s}", "preconditions": ["Signed in"], "steps": ["Open Tasks", "Save"],
              "test_data": ["Title: X"], "expected": ["Saved"]} for s in slot_ids]
    return "```json\n" + json.dumps({"cases": cases, "extras": list(extras)}) + "\n```"


SCEN = [Scenario(id="sc1", flow_id="fl", type="branches_edges", title="Admin rights"),
        Scenario(id="sc2", flow_id="fl", type="errors_failures", title="Not an admin"),
        Scenario(id="sc3", flow_id="fl", type="happy_path", title="Create")]


def test_write_scenarios_fills_missing_types(tmp_path):
    reply = "```json\n" + json.dumps({"scenarios": [{"type": "branches_edges", "title": "Admin can add", "description": "",
                                                     "inventory_ids": ["inv1"], "step_refs": [1]}]}) + "\n```"
    w = _work(tmp_path, FakeProvider([reply]))
    w.head = F([st("x")])
    scen = write_scenarios(w, SLOTS)
    assert {s.type for s in scen} == {"branches_edges", "errors_failures", "happy_path"}
    assert next(s for s in scen if s.type == "branches_edges").title == "Admin can add"


def test_cases_one_per_slot(tmp_path):
    w = _work(tmp_path, FakeProvider([_cases_reply(["s1", "s2", "s3"], [{"title": "Leap year", "steps": ["a"], "expected": ["b"]}])]))
    w.head = F([st("x")])
    cases, extras, failed = write_cases(w, SCEN, SLOTS)
    assert failed == {}
    assert [c.slot_id for c in cases] == ["s1", "s2", "s3"] and [c.priority for c in cases] == ["P0", "P0", "P1"]
    assert len(extras) == 1 and extras[0].suggested


def test_cases_batched_by_15(tmp_path):
    many = [CaseSlot(id=f"m{i}", flow_id="fl", scenario_type="branches_edges", priority="P2") for i in range(20)]
    p = FakeProvider([_cases_reply([f"m{i}" for i in range(15)]), _cases_reply([f"m{i}" for i in range(15, 20)])])
    w = _work(tmp_path, p)
    w.head = F([st("x")])
    cases, _, _ = write_cases(w, SCEN, many)
    assert len(cases) == 20 and len(p.requests) == 2


def test_cases_missing_and_unknown_slots(tmp_path):
    w = _work(tmp_path, FakeProvider([_cases_reply(["s1", "s2", "zz"])]))
    w.head = F([st("x")])
    cases, _, _ = write_cases(w, SCEN, SLOTS)
    kept, rejected = check_cases(cases, SLOTS)
    assert [c.slot_id for c in kept] == ["s1", "s2"]
    assert rejected == {"s3": "not returned"}


def test_check_cases_rejects_empty_expected(tmp_path):
    w = _work(tmp_path, FakeProvider([_cases_reply(["s1"])]))
    w.head = F([st("x")])
    cases, _, _ = write_cases(w, SCEN, SLOTS[:1])
    cases[0].expected = []
    kept, rejected = check_cases(cases, SLOTS[:1])
    assert kept == [] and rejected == {"s1": "missing steps or expected result"}


def test_a_failed_batch_keeps_the_batches_that_worked(tmp_path):
    from kb.errors import KbError

    many = [CaseSlot(id=f"m{i}", flow_id="fl", scenario_type="branches_edges", priority="P2") for i in range(20)]

    class SecondBatchFails(FakeProvider):
        def run(self, req):
            if "slot m15 " in req.prompt:
                self.requests.append(req)
                raise KbError("Claude timed out")
            return super().run(req)
    p = SecondBatchFails([_cases_reply([f"m{i}" for i in range(15)])])
    w = _work(tmp_path, p)
    w.head = F([st("x")])
    cases, _, failed = write_cases(w, SCEN, many)
    assert [c.slot_id for c in cases] == [f"m{i}" for i in range(15)]
    assert failed == {f"m{i}": "Claude timed out" for i in range(15, 20)}
