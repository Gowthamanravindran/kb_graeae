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


# ---------------------------------------------------------------- Task 5: generation in shared mode

from kb.testplan.graph_design import run_design  # noqa: E402


def _shared(team):
    ca, cb = team.client("maint@x"), team.client("contrib@x")
    shared.share(team.cfg, L, team.pid, team.a, ca)
    shared.pull(team.cfg, L, team.pid, team.b, cb)
    return ca, cb


def _gen(team, store, client, counts, provider=None):
    return run_design(team.cfg, team.pid, L, store, provider or DesignFake(), RecordingTracer(), "pr", counts, None,
                      client=client)


def test_two_users_split_generation_without_overlap(team):
    ca, cb = _shared(team)
    ra = _gen(team, team.a, ca, {"P0": 2})
    pb = DesignFake()
    rb = _gen(team, team.b, cb, {"P0": 2}, pb)
    assert (ra.written, rb.written, ra.extras_from_conflicts, rb.extras_from_conflicts) == (2, 2, 0, 0)
    assert not any("How does:" in r.prompt for r in pb.requests)  # B reused A's shared traces: no Claude trace call
    for store, client in ((team.a, ca), (team.b, cb)):
        shared.sync(team.cfg, L, team.pid, store, client)
        cases = [c for c in PlanStore(store).cases(P, L, team.pid) if not c.suggested]
        assert len(cases) == 4 and len({c.slot_id for c in cases}) == 4 and len({c.id for c in cases}) == 4
    view = ca.plan(P, L, team.pid)
    assert sum(1 for s in view["slots"] if s["status"] == "written") == 4
    assert not any(s["status"] == "reserved" for s in view["slots"])  # nothing left held


def test_busy_trace_fails_only_that_flow(team):
    ca, cb = _shared(team)
    flow = PlanStore(team.b).load_impact(P, L, team.pid).flows[0]
    ca.lock_trace(P, L, team.pid, flow.id, "base")
    res = _gen(team, team.b, cb, {"P0": 2})
    assert res.written == 0 and res.failed == 2
    failed = [s for s in PlanStore(team.b).load_slots(P, L, team.pid) if s.status == "failed"]
    assert failed and all("maint@x is tracing" in s.fail_reason for s in failed)
    assert not any(s["status"] == "reserved" for s in cb.plan(P, L, team.pid)["slots"])  # given back


def test_reservations_renewed_while_running(team, monkeypatch):
    import time

    from kb.testplan import graph_design, nodes_design

    ca, cb = _shared(team)
    monkeypatch.setattr(graph_design, "RENEW_EVERY", 0.05)
    real = nodes_design.write_cases
    monkeypatch.setattr(graph_design, "write_cases", lambda *a: (time.sleep(0.3), real(*a))[1])
    renewed = []
    orig = cb.renew
    monkeypatch.setattr(cb, "renew", lambda *a, **k: (renewed.append(a), orig(*a, **k))[1])
    _gen(team, team.b, cb, {"P0": 1})
    assert renewed


def test_impact_rerun_reshares_slots(team):
    from fixture_repos import HEAD, RESOLVERS, git

    ca, cb = _shared(team)
    git(team.fx.be, "checkout", "-q", HEAD)
    f = team.fx.be / RESOLVERS
    f.write_text(f.read_text().replace("    if (!context.user.isAdmin) {\n      throw new Error('Only admins can add tasks');\n    }\n", ""))
    git(team.fx.be, "commit", "-qam", "no admin check")
    git(team.fx.be, "checkout", "-q", "develop")
    run_impact(team.cfg, ChangeSetRequest(project=P, base=dict(team.fx.base), head=dict(team.fx.head)), team.a,
               DesignFake(), RecordingTracer(), client=ca)
    local = {s.id for s in PlanStore(team.a).load_slots(P, L, team.pid)}
    remote = {s["id"] for s in cb.plan(P, L, team.pid)["slots"]}
    assert remote == local


def test_offline_run_warns_and_marks_unreserved(team, monkeypatch):
    ca, cb = _shared(team)

    def down(*a, **k):
        raise RegistryUnavailable("registry unreachable")
    for m in ("plan", "reserve", "renew", "release", "lock_trace", "unlock_trace", "publish_plan_items", "case_ids"):
        monkeypatch.setattr(cb, m, down)
    res = _gen(team, team.b, cb, {"P0": 1})
    assert res.written == 1
    assert "Offline: these cases are not reserved and may duplicate a teammate's" in res.warnings
    [c] = [c for c in PlanStore(team.b).cases(P, L, team.pid) if not c.suggested]
    assert c.unreserved and shared.pending(team.b, P, L, team.pid) >= 1


# ---------------------------------------------------------------- Task 6: CLI

def test_cli_share_pull_sync_assign_release(team, monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from kb.cli import app

    y = tmp_path / "team.kb.yaml"
    y.write_text(team.fx.cfg_path.read_text() + "registry: http://testserver\n")
    who = {"email": "maint@x"}
    monkeypatch.setattr("kb.sync.client.client_for", lambda *a, **k: team.client(who["email"]))
    monkeypatch.setattr("kb.cli.get_provider", lambda: DesignFake())
    run = lambda *args: CliRunner().invoke(app, ["testplan", *args])  # noqa: E731
    monkeypatch.setenv("KB_HOME", str(team.a.kb_home))
    r = run("share", str(y), team.pid)
    assert r.exit_code == 0 and "shared" in r.stdout, r.output
    monkeypatch.setenv("KB_HOME", str(team.b.kb_home))
    who["email"] = "contrib@x"
    r = run("pull", str(y), team.pid, "--label", L)
    assert r.exit_code == 0, r.output
    r = run("generate", str(y), team.pid, "--p0", "1")
    assert r.exit_code == 0 and "wrote 1 cases" in r.stdout, r.output
    monkeypatch.setenv("KB_HOME", str(team.a.kb_home))
    who["email"] = "maint@x"
    r = run("sync", str(y), team.pid)
    assert r.exit_code == 0, r.output
    flow = PlanStore(team.a).load_impact(P, L, team.pid).flows[0].id
    r = run("assign", str(y), team.pid, "--flow", flow, "--to", "contrib@x")
    assert r.exit_code == 0 and "assigned" in r.stdout, r.output
    sid = next(s.id for s in PlanStore(team.a).load_slots(P, L, team.pid) if s.status == "planned")
    team.client("contrib@x").reserve(P, L, team.pid, slot_ids=[sid])
    r = run("release", str(y), team.pid, "--slot", sid)
    assert r.exit_code == 0 and sid in r.stdout, r.output


def test_cases_written_before_sharing_count_as_written_for_the_team(team):
    ca = team.client("maint@x")
    run_design(team.cfg, team.pid, L, team.a, DesignFake(), RecordingTracer(), "pr", {"P0": 2}, None)  # local, not shared yet
    shared.share(team.cfg, L, team.pid, team.a, ca)
    view = ca.plan(P, L, team.pid)
    written = [s for s in view["slots"] if s["status"] == "written"]
    assert len(written) == 2 and all(s["case_id"].startswith("TC-") for s in written)
    assert not [i for i in view["items"] if i["kind"] == "testcase" and i["payload"].get("suggested")]
