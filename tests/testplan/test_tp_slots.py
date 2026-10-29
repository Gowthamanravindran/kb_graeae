from collections import Counter

from kb.testplan.models import ImpactFlow, InventoryItem
from kb.testplan.slots import assign_priorities, estimate_calls, plan_slots, risk, select


def item(i, kind, change="added", outcomes=None, fn="AddTask", repo="be"):
    return InventoryItem(id=i, kind=kind, change=change, repo=repo, file="r.ts", lines=(1, 1), function=fn,
                         summary=kind, outcomes=outcomes or [])


INV = [item("p", "permission", outcomes=["allowed", "denied"]), item("v", "validation", outcomes=["valid", "invalid"]),
       item("b", "branch", "changed", ["true", "false"]), item("r", "removed", "removed"),
       item("f", "field", outcomes=["sent"], repo="fe")]
FLOW = ImpactFlow(id="f1", name="Screen /tasks", entry_points=["fe:route:/tasks"], inventory_ids=[i.id for i in INV],
                  changed_functions=["fe:a.tsx#C", "be:r.ts#AddTask"], crosses_repos=True, has_job=True,
                  path=["fe:route:/tasks", "fe:a.tsx", "fe:a.tsx#C", "fe:op.ts#op:Add", "be:r.ts#resolver:Add@6", "be:r.ts", "be:r.ts#AddTask"])


def _planned():
    flows = [FLOW.model_copy()]
    for f in flows:
        f.risk, f.risk_reasons = risk(f, INV)
    slots = plan_slots(flows, INV, "pr")
    assign_priorities(slots, flows, INV)
    return flows, slots


def test_fixture_slot_counts():
    flows, slots = _planned()
    non_reg = Counter(s.priority for s in slots if s.rule != "regression")
    assert non_reg == Counter({"P0": 4, "P1": 3, "P2": 2, "P3": 1})
    reg = [s for s in slots if s.rule == "regression"]
    assert 1 <= len(reg) <= 3 and {s.priority for s in reg} == {"P3"}
    assert "regression" not in {s.scenario_type for s in plan_slots(flows, INV, "scenario")}


def test_risk_reasons():
    score, reasons = risk(FLOW, INV)
    assert reasons == ["removed behaviour", "permission change", "crosses repos", "background job", "no master flow"]
    assert score == 3 + 3 + 2 + 2 + 1 + 0.5 * 5


def _two_flows():
    a = ImpactFlow(id="a", name="A", entry_points=["x"], changed_functions=[], inventory_ids=[], risk=9)
    b = ImpactFlow(id="b", name="B", entry_points=["y"], changed_functions=[], inventory_ids=[], risk=2)
    from kb.testplan.models import CaseSlot
    slots = [CaseSlot(id=f"a{i}", flow_id="a", scenario_type="branches_edges", priority="P0") for i in range(4)] + \
            [CaseSlot(id=f"b{i}", flow_id="b", scenario_type="branches_edges", priority="P0") for i in range(3)]
    return [a, b], slots


def test_cap_takes_highest_risk_first():
    flows, slots = _two_flows()
    chosen = select(slots, flows, {"P0": 5}, None)
    assert len(chosen) == 5 and sum(1 for s in chosen if s.flow_id == "a") == 4


def test_per_flow_cap():
    flows, slots = _two_flows()
    chosen = select(slots, flows, {"P0": 7}, 2)
    assert Counter(s.flow_id for s in chosen) == Counter({"a": 2, "b": 2})


def test_estimate_calls_counts_reuse():
    flows, slots = _planned()
    chosen = select(slots, flows, {"P0": 4, "P1": 3, "P2": 2, "P3": 9}, None)
    n = len(chosen)
    import math
    assert estimate_calls(chosen, flows, set(), set(), "pr") == 1 + 1 + 1 + math.ceil(n / 15)
    have = {"f1:base", "f1:head"}
    scen = {("f1", t) for t in {s.scenario_type for s in chosen}}
    assert estimate_calls(chosen, flows, have, scen, "pr") == math.ceil(n / 15)


def test_slot_ids_stable():
    _, a = _planned()
    _, b = _planned()
    assert [s.id for s in a] == [s.id for s in b] and len({s.id for s in a}) == len(a)


def test_money_flow_lifts_p1_to_p0():
    inv = [item("b", "branch", "changed", ["true", "false"], fn="deleteTask")]
    f = ImpactFlow(id="m", name="M", entry_points=["x"], changed_functions=["be:r.ts#deleteTask"], inventory_ids=["b"])
    slots = plan_slots([f], inv, "scenario")
    assign_priorities(slots, [f], inv)
    assert {s.priority for s in slots if s.rule.startswith("branch")} == {"P0"}
