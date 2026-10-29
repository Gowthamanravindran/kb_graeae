from collections import Counter

from fakes import FakeProvider, RecordingTracer
from kb.config import load_config
from kb.testplan.graph_design import run_design
from kb.testplan.scenario import plan_scenario
from kb.testplan.store import PlanStore

from agent_helpers import GOOD_STEPS, LEARN, build_pack, save_flow, trace_reply
from design_fake import DesignFake
from serve_helpers import write_cfg


def _setup(tmp_path):
    store, cos = build_pack(tmp_path)
    return load_config(write_cfg(tmp_path)), store, cos


def test_scenario_plan_counts_before_generation(tmp_path):
    cfg, store, cos = _setup(tmp_path)
    p = FakeProvider([trace_reply(GOOD_STEPS), LEARN])
    sp = plan_scenario(cfg, "main", store, p, RecordingTracer(), text="How does an admin schedule a repeating task?")
    assert not any("Write exactly one test case" in r.prompt for r in p.requests)
    slots = PlanStore(store).load_slots("mini", "main", sp.plan_id)
    assert "regression" not in {s.scenario_type for s in slots}
    assert sp.counts == dict(Counter(s.priority for s in slots)) and sum(sp.counts.values()) == len(slots) > 0
    cs = PlanStore(store).load_change_set("mini", "main", sp.plan_id)
    assert cs.kind == "scenario" and cs.scenario_text.startswith("How does an admin")


def test_scenario_from_answer_reuses_flow(tmp_path):
    cfg, store, cos = _setup(tmp_path)
    save_flow(store, cos)
    p = FakeProvider()
    sp = plan_scenario(cfg, "main", store, p, RecordingTracer(), answer_id="q1")
    assert p.requests == []
    assert sp.counts == {"P0": 1, "P1": 3}  # happy path; `task.repeat` true/false; the TaskModel.create query
    res = run_design(cfg, sp.plan_id, "main", store, DesignFake(), RecordingTracer(), "scenario", {"P0": 1, "P1": 3}, None)
    assert res.written == 4
