"""Deferred minors 1 and 2: how re-running the impact treats cases already written."""

from kb.packs.store import PackStore
from kb.testplan.inventory import _id
from kb.testplan.models import ImpactFlow, ImpactReport, InventoryItem, TestCase
from kb.testplan.nodes_impact import reconcile
from kb.testplan.slots import plan_slots
from kb.testplan.store import PlanStore

P, L, PID = "mini", "main", "pr-1"
FN = "be:src/tasks.ts#addTask"
OTHER = "be:src/notify.ts#notify"


def _item(text, kind="permission"):
    return InventoryItem(id=_id("be", "src/tasks.ts", "addTask", kind, text), kind=kind, change="changed", repo="be",
                         file="src/tasks.ts", lines=(3, 3), function="addTask", summary=text,
                         outcomes=["allowed", "denied"], text=text)


def _report(items, hashes, fns=(FN,)):
    flow = ImpactFlow(id="flow1", name="Add task", entry_points=[FN], changed_functions=list(fns),
                      inventory_ids=[i.id for i in items])
    return ImpactReport(change_set_id=PID, head_commits={}, inventory=items, flows=[flow],
                        graph_delta={"function_hashes": hashes})


def _run(ps, report, previous):
    slots = plan_slots(report.flows, report.inventory, "pr")
    reconcile(ps, P, L, PID, slots, previous, report)
    return ps.load_slots(P, L, PID)


def _write_all(ps):
    def fn(slots):
        for s in slots:
            s.status, s.case_id = "written", f"TC-{s.id[:4]}"
            ps.save_case(P, L, PID, TestCase(id=s.case_id, slot_id=s.id, title=s.rule))
    ps.update_slots(P, L, PID, fn)


def test_changed_condition_marks_its_cases_stale_not_obsolete(tmp_path):
    ps = PlanStore(PackStore(tmp_path))
    r1 = _report([_item("user.isAdmin")], {FN: "h1"})
    _run(ps, r1, None)
    _write_all(ps)
    r2 = _report([_item("user.isAdmin || user.isOwner")], {FN: "h2"})
    slots = _run(ps, r2, r1)
    perm = [s for s in slots if s.rule == "permission"]
    assert sorted(s.status for s in perm) == ["stale", "stale"]          # same two cases, now stale
    assert all(s.inventory_ids == [r2.inventory[0].id] for s in perm)     # carried onto the new decision point
    assert not [s for s in slots if s.status == "obsolete"]
    assert not [s for s in slots if s.rule == "permission" and s.status == "planned"]
    cases = {c.id: c for c in ps.cases(P, L, PID)}
    assert all(cases[s.case_id].stale and cases[s.case_id].slot_id == s.id for s in perm)


def test_unrelated_new_condition_is_still_new(tmp_path):
    ps = PlanStore(PackStore(tmp_path))
    r1 = _report([_item("user.isAdmin")], {FN: "h1"})
    _run(ps, r1, None)
    _write_all(ps)
    r2 = _report([_item("task.ownerId === me.id")], {FN: "h2"})
    slots = _run(ps, r2, r1)
    assert sorted(s.status for s in slots if s.rule == "permission") == ["obsolete", "obsolete", "planned", "planned"]


def test_function_changed_for_the_first_time_makes_happy_path_stale(tmp_path):
    ps = PlanStore(PackStore(tmp_path))
    r1 = _report([_item("user.isAdmin")], {FN: "h1"})
    _run(ps, r1, None)
    _write_all(ps)
    # a later commit also changes notify(), which the flow passes through; addTask itself is untouched
    r2 = _report([_item("user.isAdmin")], {FN: "h1", OTHER: "n1"}, fns=(FN, OTHER))
    slots = _run(ps, r2, r1)
    assert next(s for s in slots if s.rule == "happy path").status == "stale"
    assert {s.status for s in slots if s.rule == "permission"} == {"written"}


def test_report_without_hashes_does_not_mark_everything_stale(tmp_path):
    ps = PlanStore(PackStore(tmp_path))
    r1 = _report([_item("user.isAdmin")], {})
    _run(ps, r1, None)
    _write_all(ps)
    slots = _run(ps, _report([_item("user.isAdmin")], {FN: "h1"}), r1)
    assert {s.status for s in slots} == {"written"}
