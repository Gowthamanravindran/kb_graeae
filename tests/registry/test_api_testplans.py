"""Stage 5b: test plans shared through the registry."""

import pytest

from helpers import seeded
from test_api_bases import push

URL = "/api/v1/projects/mini/branches/main/testplans"


def changeset(pid="pr-1"):
    ref = {"mini-be": {"branch": "develop", "commit": "a" * 40}}
    return {"id": pid, "kind": "pr", "project": "mini", "title": "Repeat end", "base": ref, "head": ref, "base_pack": "main"}


def impact(pid="pr-1", flows=(("fl1", 3.0), ("fl2", 1.0))):
    return {"change_set_id": pid, "head_commits": {"mini-be": "b" * 40},
            "flows": [{"id": f, "name": f, "entry_points": [], "changed_functions": [], "inventory_ids": [], "risk": r}
                      for f, r in flows]}


def slot(sid, prio="P0", flow="fl1", **kw):
    return {"id": sid, "flow_id": flow, "scenario_type": "happy_path", "priority": prio, "rule": "happy path", **kw}


def share(reg, slots, email="contrib@x", pid="pr-1"):
    return reg.client(email).put(f"{URL}/{pid}", json={"changeset": changeset(pid), "impact": impact(pid), "slots": slots})


@pytest.fixture
def reg(tmp_path):
    r = seeded(tmp_path)
    assert push(r, {"a": "1"}).status_code == 201
    return r


def test_share_then_get_and_list(reg):
    assert share(reg, [slot("s1"), slot("s2", "P1")], email="maint@x").status_code == 200
    view = reg.client("contrib@x").get(f"{URL}/pr-1").json()
    assert view["changeset"]["id"] == "pr-1" and view["impact"]["change_set_id"] == "pr-1"
    assert [(s["id"], s["status"], s["priority"]) for s in view["slots"]] == [("s1", "planned", "P0"), ("s2", "planned", "P1")]
    assert view["slots"][0]["reserved_by"] is None and view["items"] == []
    rows = reg.client("view@x").get(URL).json()
    assert [(r["plan_id"], r["title"], r["kind"], r["counts_by_status"]) for r in rows] == [("pr-1", "Repeat end", "pr", {"planned": 2})]


def test_share_needs_contributor_and_a_pushed_base(reg):
    assert share(reg, [slot("s1")], email="view@x").status_code == 403
    r = reg.client("contrib@x").put("/api/v1/projects/mini/branches/nope/testplans/pr-1",
                                    json={"changeset": changeset(), "impact": impact(), "slots": []})
    assert r.status_code == 404
    assert share(reg, [], pid="../x").status_code in (400, 404)


def test_reshare_keeps_state_and_obsoletes_removed_written(reg):
    from kb_registry.models import PlanSlotRow, User

    share(reg, [slot("A"), slot("B"), slot("C")])
    with reg.S() as s:  # A written, B reserved by maint (Task 2/3 do this through routes)
        u = s.query(User).filter_by(email="maint@x").one()
        a = s.get(PlanSlotRow, (1, "main", "pr-1", "A"))
        a.status, a.case_id = "written", "TC-001"
        b = s.get(PlanSlotRow, (1, "main", "pr-1", "B"))
        from datetime import datetime, timedelta, timezone
        b.status, b.reserved_by, b.reserved_until = "reserved", u.id, datetime.now(timezone.utc) + timedelta(hours=1)
        s.commit()
    assert share(reg, [slot("B", "P1"), slot("D")]).status_code == 200
    view = {s["id"]: s for s in reg.client("contrib@x").get(f"{URL}/pr-1").json()["slots"]}
    assert set(view) == {"A", "B", "D"}
    assert (view["A"]["status"], view["A"]["case_id"]) == ("obsolete", "TC-001")
    assert (view["B"]["status"], view["B"]["reserved_by"], view["B"]["priority"]) == ("reserved", "maint@x", "P1")
    assert view["D"]["status"] == "planned"


def test_reshare_stale_from_client_wins_over_written(reg):
    from kb_registry.models import PlanSlotRow

    share(reg, [slot("A")])
    with reg.S() as s:
        a = s.get(PlanSlotRow, (1, "main", "pr-1", "A"))
        a.status, a.case_id = "written", "TC-001"
        s.commit()
    share(reg, [slot("A", status="stale", case_id="TC-001")])
    assert reg.client("contrib@x").get(f"{URL}/pr-1").json()["slots"][0]["status"] == "stale"
