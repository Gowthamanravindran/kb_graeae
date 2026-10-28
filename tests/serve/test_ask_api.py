from agent_helpers import GOOD_STEPS, LEARN, save_flow, trace_reply
from fakes import step
from serve_helpers import wait_job

Q = "How does scheduling of repeating tasks work?"


def setup(serve):
    c, settings, store, provider, cfg = serve
    c.post("/api/projects", json={"config_path": str(cfg)})
    return c, store, provider


def test_ask_job_returns_payload_with_how(serve):
    c, store, provider = setup(serve)
    provider.responses = [trace_reply(GOOD_STEPS), LEARN]
    provider.steps = [step("Read", "src/a.ts"), step("Grep", "addTask"), step("Read", "src/b.ts")]
    job = c.post("/api/projects/mini/branches/main/ask", json={"question": Q}).json()["job_id"]
    j = wait_job(c, job)
    assert j["state"] == "done", j["error"]
    p = j["result"]
    assert p["answer"].startswith("Admins") and p["reused"] is False and p["saved_qa_id"]
    assert [s["side"] for s in p["steps"]] == ["ui", "backend", "backend"] and all(s["verified"] for s in p["steps"])
    assert p["steps"][0]["citation"]["file"] == "src/tasks/TaskCreation.tsx"
    how = p["how"]
    assert how["claude_calls"] == 2 and how["files_read"] == 4 and how["tool_steps"] == 6
    assert how["modules"] == ["Tasks"] and how["shortlist_size"] > 0 and how["seconds"] >= 0
    assert p["mermaid"].startswith("flowchart LR")


def test_ask_reuse_has_zero_calls(serve):
    c, store, provider = setup(serve)
    from agent_helpers import FIX
    from kb.ingest.checkout import Checkout

    save_flow(store, {"mini-be": Checkout("mini-be", FIX / "mini-be", "y")}, question="How does scheduling work in Tasks?")
    j = wait_job(c, c.post("/api/projects/mini/branches/main/ask", json={"question": "How does scheduling work in tasks?"}).json()["job_id"])
    assert j["state"] == "done" and j["result"]["reused"] is True and j["result"]["how"]["claude_calls"] == 0


def test_ask_job_fails_with_claude_message(serve):
    c, store, provider = setup(serve)
    provider.unavailable = "not signed in"
    j = wait_job(c, c.post("/api/projects/mini/branches/main/ask", json={"question": Q}).json()["job_id"])
    assert j["state"] == "failed" and j["error"].startswith("Claude Code is not available")


def test_unknown_project_400(serve):
    c, *_ = serve
    r = c.post("/api/projects/nope/branches/main/ask", json={"question": Q})
    assert r.status_code == 400 and "unknown project" in r.json()["detail"]
