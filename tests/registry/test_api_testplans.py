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


# ---------------------------------------------------------------- Task 2: reservations and assignment

def reserve(reg, email="contrib@x", **body):
    return reg.client(email).post(f"{URL}/pr-1/reservations", json=body)


def slots(reg):
    return {s["id"]: s for s in reg.client("view@x").get(f"{URL}/pr-1").json()["slots"]}


def test_reserve_by_counts_follows_priority_then_risk(reg):
    share(reg, [slot("a", "P1", "fl1"), slot("b", "P0", "fl2"), slot("c", "P0", "fl1"), slot("d", "P0", "fl1")])
    r = reserve(reg, counts={"P0": 2})
    assert r.status_code == 200 and r.json()["granted"] == ["c", "d"]  # P0 first, riskier flow fl1 first, then id
    assert slots(reg)["c"]["reserved_by"] == "contrib@x" and slots(reg)["c"]["status"] == "reserved"
    assert reserve(reg, "maint@x", counts={"P0": 5, "P1": 1}, per_flow_cap=1).json()["granted"] == ["b", "a"]


def test_reserve_by_ids_and_flow_filter(reg):
    share(reg, [slot("a"), slot("b", flow="fl2")])
    assert reserve(reg, slot_ids=["a", "zz"]).json()["granted"] == ["a"]
    assert reserve(reg, "maint@x", counts={"P0": 5}, flow_ids=["fl2"]).json()["granted"] == ["b"]


def test_second_qa_gets_the_next_free_slots(reg):
    share(reg, [slot(f"s{i}") for i in range(4)])
    assert reserve(reg, counts={"P0": 2}).json()["granted"] == ["s0", "s1"]
    assert reserve(reg, "maint@x", counts={"P0": 2}).json()["granted"] == ["s2", "s3"]
    assert reserve(reg, "maint@x", counts={"P0": 2}).json()["granted"] == []


def test_expired_reservation_is_free_again(reg, monkeypatch):
    from datetime import timedelta

    from kb_registry.api import testplans as tp

    share(reg, [slot("a")])
    reserve(reg, slot_ids=["a"])
    later = tp._now() + timedelta(hours=24, seconds=1)
    monkeypatch.setattr(tp, "_now", lambda: later)
    assert slots(reg)["a"]["status"] == "planned" and slots(reg)["a"]["reserved_by"] is None
    assert reserve(reg, "maint@x", slot_ids=["a"]).json()["granted"] == ["a"]


def test_renew_only_own(reg):
    share(reg, [slot("a"), slot("b")])
    first = reserve(reg, slot_ids=["a"]).json()["reserved_until"]
    reserve(reg, "maint@x", slot_ids=["b"])
    r = reg.client("contrib@x").post(f"{URL}/pr-1/reservations/renew", json={"slot_ids": ["a", "b"]}).json()
    assert r["renewed"] == ["a"] and r["reserved_until"] >= first


def test_roles_for_reserve_assign_release(reg):
    share(reg, [slot("a"), slot("b"), slot("c")])
    assert reserve(reg, "view@x", slot_ids=["a"]).status_code == 403
    reserve(reg, "maint@x", slot_ids=["a"])
    c = reg.client("contrib@x")
    assert c.post(f"{URL}/pr-1/assign", json={"slot_ids": ["b"], "email": "contrib@x"}).status_code == 403
    assert c.request("DELETE", f"{URL}/pr-1/reservations", json={"slot_ids": ["a"]}).json()["released"] == []
    m = reg.client("maint@x")
    assert m.post(f"{URL}/pr-1/assign", json={"slot_ids": ["c"], "email": "maint@x"}).json()["assigned"] == ["c"]
    assert reserve(reg, slot_ids=["c"]).json()["granted"] == []          # assigned to someone else
    assert reserve(reg, "maint@x", slot_ids=["c"]).json()["granted"] == ["c"]
    reserve(reg, slot_ids=["b"])
    assert m.request("DELETE", f"{URL}/pr-1/reservations", json={"slot_ids": ["b"]}).json()["released"] == ["b"]
    assert slots(reg)["b"]["status"] == "planned"
    assert m.post(f"{URL}/pr-1/assign", json={"slot_ids": ["a"], "email": "nobody@x"}).status_code == 400


def test_assign_by_flow_and_unassign(reg):
    share(reg, [slot("a", flow="fl1"), slot("b", flow="fl1"), slot("c", flow="fl2")])
    m = reg.client("maint@x")
    assert m.post(f"{URL}/pr-1/assign", json={"flow_ids": ["fl1"], "email": "contrib@x"}).json()["assigned"] == ["a", "b"]
    assert slots(reg)["a"]["assigned_to"] == "contrib@x"
    m.post(f"{URL}/pr-1/assign", json={"slot_ids": ["a"], "email": None})
    assert slots(reg)["a"]["assigned_to"] is None


def test_assign_releases_someone_elses_reservation(reg):
    share(reg, [slot("a")])
    reserve(reg, "maint@x", slot_ids=["a"])
    reg.client("maint@x").post(f"{URL}/pr-1/assign", json={"slot_ids": ["a"], "email": "contrib@x"})
    s = slots(reg)["a"]
    assert (s["status"], s["reserved_by"], s["assigned_to"]) == ("planned", None, "contrib@x")


def test_members_list(reg):
    got = reg.client("view@x").get("/api/v1/projects/mini/members").json()
    assert {(m["email"], m["role"]) for m in got} == {("maint@x", "maintainer"), ("contrib@x", "contributor"), ("view@x", "viewer")}


@pytest.fixture
def reg_any_db(db_url, tmp_path):
    r = seeded(tmp_path, db_url)
    assert push(r, {"a": "1"}).status_code == 201
    return r


def test_concurrent_reservations_never_overlap(reg_any_db):
    import threading

    reg = reg_any_db
    share(reg, [slot(f"s{i:02d}") for i in range(20)])
    got, errors = [], []

    def worker(email):
        try:
            r = reserve(reg, email, counts={"P0": 3})
            got.append(r.json()["granted"])
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    threads = [threading.Thread(target=worker, args=("contrib@x" if i % 2 else "maint@x",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    flat = [x for g in got for x in g]
    assert not errors and len(flat) == len(set(flat)) == 20
