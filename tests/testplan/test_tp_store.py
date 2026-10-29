import pytest

from kb.errors import KbError
from kb.packs.store import PackStore
from kb.testplan.models import BranchRef, CaseSlot, ChangeSet, ImpactReport, Scenario, TestCase
from kb.testplan.store import PlanStore

from agent_helpers import build_pack


def _cs():
    ref = {"mini-fe": BranchRef(branch="master", commit="a" * 40)}
    return ChangeSet(id="cs1", project="mini", title="Repeat end", base=ref, head=ref, base_pack="main")


def test_round_trip_all_records(tmp_path):
    store, _ = build_pack(tmp_path)
    ps = PlanStore(store)
    ps.save_change_set("main", _cs())
    ps.save_impact("mini", "main", ImpactReport(change_set_id="cs1", head_commits={"mini-fe": "b" * 40}))
    ps.save_slots("mini", "main", "cs1", [CaseSlot(id="s1", flow_id="f", scenario_type="happy_path", priority="P0")])
    ps.save_scenario("mini", "main", "cs1", Scenario(id="sc1", flow_id="f", type="happy_path", title="T"))
    ps.save_case("mini", "main", "cs1", TestCase(id="TC-001", title="Create", slot_id="s1", priority="P0"))
    assert ps.load_change_set("mini", "main", "cs1").title == "Repeat end"
    assert ps.load_impact("mini", "main", "cs1").head_commits == {"mini-fe": "b" * 40}
    assert [s.id for s in ps.load_slots("mini", "main", "cs1")] == ["s1"]
    assert [s.id for s in ps.scenarios("mini", "main", "cs1")] == ["sc1"]
    assert [c.id for c in ps.cases("mini", "main", "cs1")] == ["TC-001"]
    rows = ps.list_plans("mini")
    assert [(r.id, r.kind, r.label) for r in rows] == [("cs1", "pr", "main")]
    assert rows[0].counts_by_status == {"planned": 1} and rows[0].counts_by_priority == {"P0": 1}
    assert ps.find("mini", "cs1") == "main"


def test_next_case_id_never_reused(tmp_path):
    store, _ = build_pack(tmp_path)
    ps = PlanStore(store)
    ps.save_change_set("main", _cs())
    a, b = ps.next_case_id("mini", "main", "cs1"), ps.next_case_id("mini", "main", "cs1")
    assert (a, b) == ("TC-001", "TC-002")
    ps.save_case("mini", "main", "cs1", TestCase(id=b, title="x"))
    ps.delete_case("mini", "main", "cs1", b)
    assert ps.next_case_id("mini", "main", "cs1") == "TC-003"


def test_unsafe_plan_id_refused(tmp_path):
    ps = PlanStore(PackStore(tmp_path / "home"))
    with pytest.raises(KbError):
        ps.dir("mini", "main", str(tmp_path / "evil"))
    with pytest.raises(KbError):
        ps.dir("mini", "main", "../x")
