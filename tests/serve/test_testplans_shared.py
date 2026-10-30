"""Stage 5b engine routes: share, pull, sync, assign, release and people, against an in-process registry."""

import shutil

import pytest
from fastapi.testclient import TestClient

from kb.config import load_config
from kb.packs.store import PackStore
from kb.serve.app import ServeSettings, create_app
from kb.sync.client import RegistryClient
from kb.sync.commands import push_pack

from design_fake import DesignFake
from fixture_repos import analyse_base, make_pr_fixture
from helpers import seeded
from serve_helpers import TOKEN
from test_testplans import wait

L = "production"


class Setup:
    def __init__(self, tmp, push=True):
        self.fx = make_pr_fixture(tmp / "fx")
        y = tmp / "mini.kb.yaml"
        y.write_text(self.fx.cfg_path.read_text() + "registry: http://testserver\n")
        self.cfg_path = y
        self.cfg, self.store = analyse_base(self.fx, tmp / "home")
        self.reg = seeded(tmp / "registry")
        if push:
            push_pack(load_config(y), L, self.store, self.rc("maint@x"))
        self.tmp = tmp

    def rc(self, email):
        return RegistryClient("http://testserver", self.reg.tokens[email], http=TestClient(self.reg.app))

    def engine(self, home, email):
        s = ServeSettings(kb_home=home, token=TOKEN, provider_factory=lambda: DesignFake(),
                          client_factory=lambda cfg=None, registry=None: self.rc(email))
        c = TestClient(create_app(s), base_url="http://127.0.0.1:5555")
        c.headers["X-KB-Token"] = TOKEN
        c.post("/api/projects", json={"config_path": str(self.cfg_path)})
        return c

    def plan(self, c):
        r = c.post("/api/projects/mini/testplans/pr", json={"base": dict(self.fx.base), "head": dict(self.fx.head), "pr_links": {}})
        j = wait(c, r.json()["job_id"])
        assert j["state"] == "done", j["error"]
        return j["result"]["plan_id"]


@pytest.fixture
def s(tmp_path):
    return Setup(tmp_path)


def test_share_then_teammate_pulls_and_generates(s):
    a = s.engine(s.tmp / "home", "maint@x")
    pid = s.plan(a)
    j = wait(a, a.post(f"/api/projects/mini/testplans/{pid}/share").json()["job_id"])
    assert j["state"] == "done", j["error"]
    assert a.get(f"/api/projects/mini/testplans/{pid}").json()["shared"] is True
    home_b = s.tmp / "home-b"
    shutil.copytree(PackStore(s.tmp / "home").pack_dir("mini", L), PackStore(home_b).pack_dir("mini", L),
                    ignore=shutil.ignore_patterns("testplans"))
    b = s.engine(home_b, "contrib@x")
    rows = b.get("/api/projects/mini/testplans/shared").json()
    assert [(r["plan_id"], r["label"], r["local"]) for r in rows] == [(pid, L, False)]
    j = wait(b, b.post(f"/api/projects/mini/testplans/shared/{L}/{pid}/pull").json()["job_id"])
    assert j["state"] == "done", j["error"]
    j = wait(b, b.post(f"/api/projects/mini/testplans/{pid}/generate", json={"counts": {"P0": 1}}).json()["job_id"])
    assert j["state"] == "done" and j["result"]["written"] == 1, j
    wait(a, a.post(f"/api/projects/mini/testplans/{pid}/sync").json()["job_id"])
    cases = a.get(f"/api/projects/mini/testplans/{pid}").json()["cases"]
    assert len(cases) == 1 and cases[0]["author"]
    people = a.get(f"/api/projects/mini/testplans/{pid}/people").json()
    assert people["me"] == "maint@x" and people["role"] == "maintainer"
    assert {p["email"]: p["written"] for p in people["progress"]}["contrib@x"] == 1
    assert {m["email"] for m in people["members"]} == {"maint@x", "contrib@x", "view@x"}


def test_assign_and_release_proxy_roles(s):
    a = s.engine(s.tmp / "home", "maint@x")
    pid = s.plan(a)
    wait(a, a.post(f"/api/projects/mini/testplans/{pid}/share").json()["job_id"])
    flow = a.get(f"/api/projects/mini/testplans/{pid}").json()["impact"]["flows"][0]["id"]
    r = a.post(f"/api/projects/mini/testplans/{pid}/assign", json={"flow_ids": [flow], "email": "contrib@x"})
    assert r.status_code == 200 and r.json()["assigned"]
    slots = a.get(f"/api/projects/mini/testplans/{pid}").json()["slots"]
    assert all(x["assigned_to"] == "contrib@x" for x in slots if x["flow_id"] == flow)
    sid = slots[0]["id"]
    s.rc("contrib@x").reserve("mini", L, pid, slot_ids=[sid])
    assert a.post(f"/api/projects/mini/testplans/{pid}/release", json={"slot_ids": [sid]}).json()["released"] == [sid]
    v = s.engine(s.tmp / "home", "view@x")
    assert v.post(f"/api/projects/mini/testplans/{pid}/assign", json={"slot_ids": [sid], "email": None}).status_code == 403


def test_share_without_pushed_base_says_push_first(tmp_path):
    s = Setup(tmp_path, push=False)
    a = s.engine(tmp_path / "home", "maint@x")
    pid = s.plan(a)
    j = wait(a, a.post(f"/api/projects/mini/testplans/{pid}/share").json()["job_id"])
    assert j["state"] == "failed" and "Push production to the registry first" in j["error"]


def test_share_needs_a_registry(tmp_path):
    s = Setup(tmp_path, push=False)
    s.cfg_path.write_text(s.fx.cfg_path.read_text())  # no registry: line
    a = s.engine(tmp_path / "home", "maint@x")
    pid = s.plan(a)
    r = a.post(f"/api/projects/mini/testplans/{pid}/share")
    assert r.status_code == 400 and "registry" in r.json()["detail"]
