import csv
import io
import threading
import time

import pytest
from fastapi.testclient import TestClient

from kb.serve.app import ServeSettings, create_app

from design_fake import DesignFake
from fixture_repos import HEAD, analyse_base, make_pr_fixture
from serve_helpers import TOKEN


def wait(c, job_id, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"/api/jobs/{job_id}").json()
        if j["state"] in ("done", "failed"):
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


@pytest.fixture
def app(tmp_path):
    fx = make_pr_fixture(tmp_path / "fx")
    analyse_base(fx, tmp_path / "home")
    provider = DesignFake()
    c = TestClient(create_app(ServeSettings(kb_home=tmp_path / "home", token=TOKEN, provider_factory=lambda: provider)),
                   base_url="http://127.0.0.1:5555")
    c.headers["X-KB-Token"] = TOKEN
    c.post("/api/projects", json={"config_path": str(fx.cfg_path)})
    return c, fx, provider


def _pr(c, fx):
    r = c.post("/api/projects/mini/testplans/pr", json={"base": dict(fx.base), "head": dict(fx.head), "pr_links": {}})
    assert r.status_code == 200, r.text
    j = wait(c, r.json()["job_id"])
    assert j["state"] == "done", j["error"]
    return j["result"]["plan_id"]


def test_pr_plan_then_detail_and_list(app):
    c, fx, _ = app
    pid = _pr(c, fx)
    rows = c.get("/api/projects/mini/testplans?kind=pr").json()
    assert [r["id"] for r in rows] == [pid] and rows[0]["label"] == "production"
    assert c.get("/api/projects/mini/testplans?kind=scenario").json() == []
    d = c.get(f"/api/projects/mini/testplans/{pid}").json()
    assert len(d["impact"]["flows"]) == 1 and len(d["impact"]["inventory"]) == 5
    assert d["change_set"]["head"]["mini-be"]["branch"] == HEAD
    assert d["summary"]["counts_by_priority"]["P0"] == 4 and d["cases"] == [] and d["extras"] == []
    assert set(d["coverage"]["items"].values()) == {"skipped"}


def test_generate_then_export(app):
    c, fx, _ = app
    pid = _pr(c, fx)
    j = wait(c, c.post(f"/api/projects/mini/testplans/{pid}/generate", json={"counts": {"P0": 2}}).json()["job_id"])
    assert j["state"] == "done" and j["result"]["written"] == 2
    r = c.get(f"/api/projects/mini/testplans/{pid}/export.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"] == f'attachment; filename="mini-{pid}-test-cases.csv"'
    assert len(list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))) == 3
    r = c.get(f"/api/projects/mini/testplans/{pid}/export.csv?priority=P3")
    assert len(list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))) == 1


def test_generate_409_while_running(app, monkeypatch):
    c, fx, _ = app
    pid = _pr(c, fx)
    gate = threading.Event()
    import kb.testplan.graph_design as gd
    real = gd.run_design
    monkeypatch.setattr(gd, "run_design", lambda *a, **k: (gate.wait(10), real(*a, **k))[1])
    first = c.post(f"/api/projects/mini/testplans/{pid}/generate", json={"counts": {"P0": 1}})
    second = c.post(f"/api/projects/mini/testplans/{pid}/generate", json={"counts": {"P0": 1}})
    gate.set()
    assert first.status_code == 200 and second.status_code == 409
    wait(c, first.json()["job_id"])


def test_extras_accept_and_dismiss(app):
    c, fx, provider = app
    pid = _pr(c, fx)
    provider.extras = 2
    wait(c, c.post(f"/api/projects/mini/testplans/{pid}/generate", json={"counts": {"P0": 1}}).json()["job_id"])
    extras = c.get(f"/api/projects/mini/testplans/{pid}").json()["extras"]
    assert len(extras) == 2
    assert c.post(f"/api/projects/mini/testplans/{pid}/extras/{extras[0]['id']}/accept").status_code == 200
    assert c.post(f"/api/projects/mini/testplans/{pid}/extras/{extras[1]['id']}/dismiss").status_code == 200
    d = c.get(f"/api/projects/mini/testplans/{pid}").json()
    assert d["extras"] == [] and any(x["id"] == extras[0]["id"] for x in d["cases"])
    assert any(s["rule"] == "suggested" and s["case_id"] == extras[0]["id"] for s in d["slots"])


def test_retry_one_slot(app):
    c, fx, _ = app
    pid = _pr(c, fx)
    slots = c.get(f"/api/projects/mini/testplans/{pid}").json()["slots"]
    target = next(s for s in slots if s["priority"] == "P2")
    j = wait(c, c.post(f"/api/projects/mini/testplans/{pid}/slots/{target['id']}/retry").json()["job_id"])
    assert j["state"] == "done" and j["result"]["written"] == 1
    after = {s["id"]: s for s in c.get(f"/api/projects/mini/testplans/{pid}").json()["slots"]}
    assert after[target["id"]]["status"] == "written"
    assert sum(1 for s in after.values() if s["status"] == "written") == 1


def test_scenario_plan_route(app):
    c, fx, _ = app
    r = c.post("/api/projects/mini/testplans/scenario", json={"label": "production", "text": "How does an admin add a task?"})
    j = wait(c, r.json()["job_id"])
    assert j["state"] == "done", j["error"]
    assert j["result"]["plan_id"].startswith("sc-") and sum(j["result"]["counts"].values()) > 0
    assert [p["kind"] for p in c.get("/api/projects/mini/testplans?kind=scenario").json()] == ["scenario"]


def test_unknown_plan_404(app):
    c, *_ = app
    assert c.get("/api/projects/mini/testplans/nope").status_code == 404


def test_generate_only_ticked_flows(app):
    c, fx, _ = app
    pid = _pr(c, fx)
    fid = c.get(f"/api/projects/mini/testplans/{pid}").json()["impact"]["flows"][0]["id"]
    j = wait(c, c.post(f"/api/projects/mini/testplans/{pid}/generate",
                       json={"counts": {"P0": 2}, "flow_ids": ["someone-else"]}).json()["job_id"])
    assert j["result"]["written"] == 0
    j = wait(c, c.post(f"/api/projects/mini/testplans/{pid}/generate", json={"counts": {"P0": 2}, "flow_ids": [fid]}).json()["job_id"])
    assert j["result"]["written"] == 2
