import threading
import time

import pytest
from fastapi.testclient import TestClient

from kb.errors import KbError, LLMUnavailable
from kb.serve.jobs import JobConflict, JobRunner

from serve_helpers import TOKEN


def wait(runner, job_id, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        j = runner.get(job_id)
        if j.state in ("done", "failed"):
            return j
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_job_runs_and_reports_steps():
    r = JobRunner()
    seen = []

    def fn(rep):
        rep.step("Checking out code", 0.1)
        seen.append(r.get(rep.job.id).step)
        rep.step("Building the code graph", 0.5)
        return {"ok": 1}
    job = r.submit("analyze", fn)
    j = wait(r, job.id)
    assert j.state == "done" and j.result == {"ok": 1} and j.progress == 1.0
    assert seen == ["Checking out code"]


@pytest.mark.parametrize("exc,msg", [
    (KbError("no pack"), "no pack"),
    (LLMUnavailable("not signed in"), "Claude Code is not available: not signed in"),
    (ValueError("boom"), "unexpected error: ValueError: boom"),
])
def test_failed_job_maps_errors(exc, msg, tmp_path):
    r = JobRunner(log_dir=tmp_path)

    def fn(rep):
        raise exc
    j = wait(r, r.submit("ask", fn).id)
    assert j.state == "failed" and j.error == msg
    if isinstance(exc, ValueError):
        assert "Traceback" in (tmp_path / "serve.log").read_text()


def test_same_key_conflicts():
    r = JobRunner()
    gate = threading.Event()
    first = r.submit("analyze", lambda rep: gate.wait(5), key="analyze:mini@main")
    with pytest.raises(JobConflict) as e:
        r.submit("analyze", lambda rep: None, key="analyze:mini@main")
    assert e.value.job.id == first.id
    gate.set()
    wait(r, first.id)
    r.submit("analyze", lambda rep: None, key="analyze:mini@main")  # free again


def test_same_key_conflicts_409(serve):
    c, *_ = serve
    from kb.serve.jobs import submit_or_409

    gate = threading.Event()

    @c.app.post("/api/_probe")
    def probe():
        return {"job_id": submit_or_409(c.app.state.jobs, "analyze", lambda rep: gate.wait(5), key="k").id}

    first = c.post("/api/_probe").json()["job_id"]
    r = c.post("/api/_probe")
    assert r.status_code == 409 and r.json()["job_id"] == first
    gate.set()


def test_job_survives_and_is_listed(serve):
    c, *_ = serve
    gate = threading.Event()
    job = c.app.state.jobs.submit("ask", lambda rep: (gate.wait(5), {"answer": "A"})[1])
    reloaded = TestClient(c.app, base_url="http://127.0.0.1:5555")  # renderer reload = new client
    reloaded.headers["X-KB-Token"] = TOKEN
    active = reloaded.get("/api/jobs?kind=ask&active=1").json()
    assert [j["id"] for j in active] == [job.id] and active[0]["state"] in ("queued", "running")
    gate.set()
    wait(c.app.state.jobs, job.id)
    d = reloaded.get(f"/api/jobs/{job.id}").json()
    assert d["state"] == "done" and d["result"] == {"answer": "A"}
    assert reloaded.get("/api/jobs/nope").status_code == 404


def test_writer_blocks_readers_on_same_branch(serve):
    from kb.serve.jobs import branch_scope

    c, *_ = serve
    gate = threading.Event()
    c.app.state.jobs.submit("analyze", lambda rep: gate.wait(5), scope=branch_scope("mini", "main"), exclusive=True)
    try:
        base = "/api/projects/mini/branches/main"
        c.post("/api/projects", json={"config_path": str(serve[4])})
        for method, path, body in [("post", "/ask", {"question": "q"}), ("post", "/pull", {"force": False}),
                                   ("post", "/analyze", {"resume": False}), ("post", "/push", None),
                                   ("get", "/map", None), ("get", "/memory", None)]:
            r = getattr(c, method)(base + path, **({"json": body} if body is not None else {}))
            assert r.status_code == 409, (path, r.status_code, r.text)
            assert "being updated" in r.text
        assert c.get("/api/projects/mini/branches/other/map").status_code != 409  # other branches unaffected
    finally:
        gate.set()


def test_reader_blocks_writer_on_same_branch(serve):
    from kb.serve.jobs import branch_scope

    c, *_ = serve
    c.post("/api/projects", json={"config_path": str(serve[4])})
    gate = threading.Event()
    c.app.state.jobs.submit("ask", lambda rep: gate.wait(5), scope=branch_scope("mini", "main"))
    try:
        r = c.post("/api/projects/mini/branches/main/analyze", json={"resume": False})
        assert r.status_code == 409 and "still running" in r.text
        r = c.post("/api/projects/mini/branches/main/ask", json={"question": "q2"})
        assert r.status_code == 200  # readers share
    finally:
        gate.set()


def test_ask_runs_while_two_analyses_block():
    r = JobRunner(max_workers=2)
    gate = threading.Event()
    a1 = r.submit("analyze", lambda rep: gate.wait(10))
    a2 = r.submit("analyze", lambda rep: gate.wait(10))
    q = r.submit("ask", lambda rep: 1)
    try:
        wait(r, q.id, timeout=5)
        assert r.get(q.id).state == "done"
        assert r.get(a1.id).state == "running" and r.get(a2.id).state == "running"
    finally:
        gate.set()


def test_queued_job_reports_position():
    r = JobRunner(max_workers=2)
    gate = threading.Event()
    r.submit("analyze", lambda rep: gate.wait(10))
    r.submit("push", lambda rep: gate.wait(10))
    third = r.submit("pull", lambda rep: None)
    try:
        import time as _t
        _t.sleep(0.2)
        d = r.get(third.id).to_dict()
        assert d["state"] == "queued" and d["queue_position"] == 1
    finally:
        gate.set()
