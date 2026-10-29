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


def test_scenario_uses_the_innermost_symbol(tmp_path):
    from kb.testplan.scenario import innermost

    spans = [("TaskService", 1, 40), (".create", 5, 12), (".remove", 14, 20), ("plain", 42, 50)]
    assert innermost(spans, 6, 8) == [(".create", 5, 12)]
    assert innermost(spans, 2, 3) == [("TaskService", 1, 40)]   # cited code outside any method: the class
    assert innermost(spans, 10, 16) == [(".create", 5, 12), (".remove", 14, 20)]


def test_replanning_keeps_removed_cases_as_obsolete_and_marks_edits_stale(tmp_path):
    import shutil

    from agent_helpers import FIX
    from kb.testplan.models import TestCase

    be = tmp_path / "be"
    shutil.copytree(FIX / "mini-be", be)
    store, cos = build_pack(tmp_path, be_root=be)
    y = tmp_path / "mini.kb.yaml"
    y.write_text(f"project: mini\nrepos:\n  - {{name: mini-fe, role: frontend, local_path: {FIX / 'mini-fe'}}}\n"
                 f"  - {{name: mini-be, role: backend, local_path: {be}}}\n")
    cfg = load_config(y)
    save_flow(store, cos)
    ps = PlanStore(store)
    sp = plan_scenario(cfg, "main", store, FakeProvider(), RecordingTracer(), answer_id="q1")

    def write(slots):
        for s in slots:
            s.status, s.case_id = "written", f"TC-{s.id[:4]}"
            ps.save_case("mini", "main", sp.plan_id, TestCase(id=s.case_id, slot_id=s.id, title=s.rule))
    ps.update_slots("mini", "main", sp.plan_id, write)
    # the repeat branch is deleted from the code on this branch
    r = be / "src/module/tasks/task/resolvers.ts"
    r.write_text(r.read_text().replace("    if (task.repeat) {\n      scheduleRepeat(task);\n    }\n", "    scheduleRepeat(task);\n"))
    plan_scenario(cfg, "main", store, FakeProvider(), RecordingTracer(), answer_id="q1")
    slots = ps.load_slots("mini", "main", sp.plan_id)
    branch = [s for s in slots if s.rule == "branch"]
    assert len(branch) == 2 and {s.status for s in branch} == {"obsolete"} and all(s.case_id for s in branch)
    assert next(s for s in slots if s.rule == "happy path").status == "stale"
    assert {c.status for c in ps.cases("mini", "main", sp.plan_id) if c.id in {s.case_id for s in branch}} == {"obsolete"}
