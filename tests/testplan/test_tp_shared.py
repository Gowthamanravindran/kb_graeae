"""Stage 5b client side: sharing a test plan through the registry (a real in-process kb-registry)."""

import shutil

import pytest
from fastapi.testclient import TestClient

from fakes import RecordingTracer
from kb.errors import KbError, RegistryUnavailable
from kb.packs.store import PackStore
from kb.sync.client import RegistryClient
from kb.sync.commands import push_pack
from kb.testplan import shared
from kb.testplan.graph_impact import ChangeSetRequest, run_impact
from kb.testplan.models import TestCase
from kb.testplan.store import PlanStore

from design_fake import DesignFake
from fixture_repos import analyse_base, make_pr_fixture
from helpers import seeded

P, L = "mini", "production"


class Team:
    def __init__(self, tmp):
        self.fx = make_pr_fixture(tmp / "fx")
        self.cfg, self.a = analyse_base(self.fx, tmp / "home-a")
        self.reg = seeded(tmp / "registry")
        push_pack(self.cfg, L, self.a, self.client("maint@x"))
        self.b = PackStore(tmp / "home-b")  # the second QA pulled the analysis (a copy, no test plans)
        shutil.copytree(self.a.pack_dir(P, L), self.b.pack_dir(P, L), ignore=shutil.ignore_patterns("testplans"))
        rep = run_impact(self.cfg, ChangeSetRequest(project=P, base=dict(self.fx.base), head=dict(self.fx.head)),
                         self.a, DesignFake(), RecordingTracer())
        self.pid = rep.change_set_id

    def client(self, email):
        return RegistryClient("http://testserver", self.reg.tokens[email], http=TestClient(self.reg.app))


@pytest.fixture
def team(tmp_path):
    return Team(tmp_path)


def _case(cid, sid, title="t"):
    return TestCase(id=cid, slot_id=sid, title=title, steps=["Do"], expected=["Done"])


def test_share_then_second_user_pulls_the_plan(team):
    shared.share(team.cfg, L, team.pid, team.a, team.client("maint@x"))
    assert PlanStore(team.a).load_change_set(P, L, team.pid).shared
    shared.pull(team.cfg, L, team.pid, team.b, team.client("contrib@x"))
    pb = PlanStore(team.b)
    assert pb.load_change_set(P, L, team.pid).shared
    assert pb.load_impact(P, L, team.pid).change_set_id == team.pid
    a_slots = {s.id for s in PlanStore(team.a).load_slots(P, L, team.pid)}
    assert {s.id for s in pb.load_slots(P, L, team.pid)} == a_slots and a_slots


def test_reserve_and_publish_then_the_other_user_sees_it(team):
    ca, cb = team.client("maint@x"), team.client("contrib@x")
    shared.share(team.cfg, L, team.pid, team.a, ca)
    shared.pull(team.cfg, L, team.pid, team.b, cb)
    granted = shared.reserve(team.cfg, L, team.pid, team.a, ca, counts={"P0": 1})
    [cid] = shared.new_case_ids(team.cfg, L, team.pid, ca, 1)
    res = shared.publish_cases(team.cfg, L, team.pid, [_case(cid, granted[0])], team.a, ca)
    assert res.accepted == [cid] and res.extras == []
    shared.sync(team.cfg, L, team.pid, team.b, cb)
    sb = {s.id: s for s in PlanStore(team.b).load_slots(P, L, team.pid)}
    assert sb[granted[0]].status == "written" and sb[granted[0]].case_id == cid
    assert [c.id for c in PlanStore(team.b).cases(P, L, team.pid)] == [cid]


def test_publish_conflict_becomes_suggested_extra(team):
    ca, cb = team.client("maint@x"), team.client("contrib@x")
    shared.share(team.cfg, L, team.pid, team.a, ca)
    shared.pull(team.cfg, L, team.pid, team.b, cb)
    sid = PlanStore(team.a).load_slots(P, L, team.pid)[0].id
    [x, y] = shared.new_case_ids(team.cfg, L, team.pid, ca, 2)
    shared.publish_cases(team.cfg, L, team.pid, [_case(x, sid)], team.a, ca)
    res = shared.publish_cases(team.cfg, L, team.pid, [_case(y, sid, "B's version")], team.b, cb)
    assert res.accepted == [] and len(res.extras) == 1
    extra = next(c for c in PlanStore(team.b).cases(P, L, team.pid) if c.id == res.extras[0])
    assert extra.suggested and extra.status == "suggested" and extra.slot_id is None and extra.title == "B's version"
    shared.sync(team.cfg, L, team.pid, team.a, ca)
    assert any(c.suggested and c.title == "B's version" for c in PlanStore(team.a).cases(P, L, team.pid))


def test_offline_cases_sync_later(team, monkeypatch):
    ca, cb = team.client("maint@x"), team.client("contrib@x")
    shared.share(team.cfg, L, team.pid, team.a, ca)
    shared.pull(team.cfg, L, team.pid, team.b, cb)
    s1, s2 = [s.id for s in PlanStore(team.a).load_slots(P, L, team.pid)[:2]]
    [taken] = shared.new_case_ids(team.cfg, L, team.pid, ca, 1)
    shared.publish_cases(team.cfg, L, team.pid, [_case(taken, s2)], team.a, ca)   # A writes s2 meanwhile

    def down(*a, **k):
        raise RegistryUnavailable("registry unreachable")
    monkeypatch.setattr(cb, "case_ids", down)
    monkeypatch.setattr(cb, "publish_plan_items", down)
    ids = shared.new_case_ids(team.cfg, L, team.pid, cb, 2)   # offline: temporary ids
    assert all(i.startswith("TCL-") for i in ids)
    res = shared.publish_cases(team.cfg, L, team.pid, [_case(ids[0], s1), _case(ids[1], s2)], team.b, cb)
    assert res.queued == ids
    assert all(c.unreserved for c in PlanStore(team.b).cases(P, L, team.pid))
    monkeypatch.undo()
    got = shared.sync(team.cfg, L, team.pid, team.b, cb)
    assert (got.accepted, got.extras) == (1, 1)                 # s1 was free; s2 had been written by A
    cases = {c.slot_id: c for c in PlanStore(team.b).cases(P, L, team.pid) if not c.suggested}
    assert cases[s1].id.startswith("TC-") and not cases[s1].id.startswith("TCL-")
    assert not any(c.id.startswith("TCL-") for c in PlanStore(team.b).cases(P, L, team.pid))


def test_pull_requires_the_base_pack(team, tmp_path):
    shared.share(team.cfg, L, team.pid, team.a, team.client("maint@x"))
    with pytest.raises(KbError, match=f"Pull {L} first"):
        shared.pull(team.cfg, L, team.pid, PackStore(tmp_path / "home-c"), team.client("contrib@x"))
