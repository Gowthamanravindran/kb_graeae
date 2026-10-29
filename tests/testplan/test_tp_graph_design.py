from collections import Counter

import pytest

from fakes import RecordingTracer
from kb.testplan.graph_design import run_design
from kb.testplan.graph_impact import ChangeSetRequest, run_impact
from kb.testplan.slots import assign_priorities, plan_slots
from kb.testplan.store import PlanStore

from design_fake import DesignFake
from fixture_repos import analyse_base, make_pr_fixture


@pytest.fixture
def plan(tmp_path):
    fx = make_pr_fixture(tmp_path / "fx")
    cfg, store = analyse_base(fx, tmp_path / "home")
    rep = run_impact(cfg, ChangeSetRequest(project="mini", base=dict(fx.base), head=dict(fx.head)), store, DesignFake(),
                     RecordingTracer())
    return cfg, store, rep.change_set_id


def _slots(store, pid):
    return PlanStore(store).load_slots("mini", "production", pid)


def test_design_generates_exact_counts(plan):
    cfg, store, pid = plan
    res = run_design(cfg, pid, "production", store, DesignFake(), RecordingTracer(), "pr", {"P0": 2}, None)
    assert res.written == 2 and res.failed == 0
    slots = _slots(store, pid)
    written = [s for s in slots if s.status == "written"]
    assert len(written) == 2 and {s.priority for s in written} == {"P0"}
    assert not [s for s in slots if s.status == "reserved"]
    cases = PlanStore(store).cases("mini", "production", pid)
    assert sorted(c.id for c in cases) == ["TC-001", "TC-002"] and all(c.code_refs for c in cases)


def test_more_continues_by_priority(plan):
    cfg, store, pid = plan
    run_design(cfg, pid, "production", store, DesignFake(), RecordingTracer(), "pr", {"P0": 2}, None)
    p = DesignFake()
    res = run_design(cfg, pid, "production", store, p, RecordingTracer(), "more", {"P0": 2, "P1": 3}, None)
    assert res.written == 5
    assert Counter(s.priority for s in _slots(store, pid) if s.status == "written") == Counter({"P0": 4, "P1": 3})
    assert not any("Return 3-12 steps" in r.prompt for r in p.requests)  # traces reused from the first run


def test_one_flow_failing_does_not_stop_others(plan):
    cfg, store, pid = plan
    ps = PlanStore(store)
    rep = ps.load_impact("mini", "production", pid)
    broken = rep.flows[0].model_copy(update={"id": "broken1", "name": "Broken flow", "risk": 0.1})
    rep.flows.append(broken)
    extra = plan_slots([broken], rep.inventory, "pr")
    assign_priorities(extra, rep.flows, rep.inventory)
    ps.save_impact("mini", "production", rep)
    ps.save_slots("mini", "production", pid, _slots(store, pid) + extra)
    res = run_design(cfg, pid, "production", store, DesignFake(fail_on="Broken flow"), RecordingTracer(), "pr",
                     {"P0": 20, "P1": 20}, None)
    slots = _slots(store, pid)
    assert res.written > 0 and res.failed > 0
    assert {s.status for s in slots if s.flow_id == "broken1" and s.priority in ("P0", "P1")} == {"failed"}
    assert all(s.fail_reason for s in slots if s.status == "failed")


def test_coverage_counts_skipped_vs_failed(plan):
    cfg, store, pid = plan
    res = run_design(cfg, pid, "production", store, DesignFake(), RecordingTracer(), "pr", {"P0": 2}, None)
    cov = res.coverage
    assert set(cov.items.values()) <= {"covered", "skipped"} and "skipped" in cov.items.values()
    assert cov.by_status.get("written") == 2


def test_extras_saved_separately(plan):
    cfg, store, pid = plan
    res = run_design(cfg, pid, "production", store, DesignFake(extras=1), RecordingTracer(), "pr", {"P0": 1}, None)
    assert res.written == 1 and res.extras == 1
    cases = PlanStore(store).cases("mini", "production", pid)
    assert [c.status for c in cases if c.suggested] == ["suggested"]


def test_langfuse_span_per_flow(plan):
    cfg, store, pid = plan
    tr = RecordingTracer()
    run_design(cfg, pid, "production", store, DesignFake(), tr, "pr", {"P0": 1}, None)
    names = [s.name for s in tr.all]
    assert any("flow:" in n for n in names)
